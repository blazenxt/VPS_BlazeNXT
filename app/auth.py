import asyncio,hashlib,re,smtplib
from datetime import datetime,timezone
from email.message import EmailMessage
from urllib.parse import urlencode
import httpx
from fastapi import APIRouter,Depends,Form,HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import get_settings
from app.db import get_db
from app.models import AuthIdentity,AuthTokenUse,User
from app.security import read_magic_link,read_oauth_state,read_session,sign_magic_link,sign_oauth_state,sign_session
s=get_settings();router=APIRouter();templates=Jinja2Templates(directory='templates');EMAIL=re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
def providers():return {'google':bool(s.google_client_id and s.google_client_secret),'github':bool(s.github_client_id and s.github_client_secret),'email':bool(s.smtp_host and s.smtp_from)}
def session_data(request):return read_session(request.cookies.get('blaze_session',''))
def synthetic_telegram_id(provider,subject):return -1-int.from_bytes(hashlib.sha256(f'{provider}:{subject}'.encode()).digest()[:7],'big')
def login_response(uid):
    r=RedirectResponse('/dashboard',303);r.set_cookie('blaze_session',sign_session(uid),httponly=True,secure=s.production,samesite='lax',max_age=s.session_ttl_seconds);return r
def identity_login(db,provider,subject,email,name,avatar=None,link_uid=None):
    identity=db.scalar(select(AuthIdentity).where(AuthIdentity.provider==provider,AuthIdentity.subject==str(subject)))
    if identity:
        identity.last_login_at=datetime.now(timezone.utc);identity.email=email or identity.email;identity.display_name=name or identity.display_name;user=db.get(User,identity.user_id);db.commit();return user
    user=db.get(User,int(link_uid)) if link_uid else None
    if not user and email:user=db.scalar(select(User).join(AuthIdentity,AuthIdentity.user_id==User.id).where(AuthIdentity.email==email.lower()))
    if not user:
        sid=synthetic_telegram_id(provider,str(subject))
        while db.scalar(select(User).where(User.telegram_id==sid)):sid-=1
        user=User(telegram_id=sid,username=(email.split('@')[0][:64] if email else f'{provider}_{subject}'[:64]),display_name=(name or email or f'{provider.title()} User')[:128]);db.add(user);db.flush()
    db.add(AuthIdentity(user_id=user.id,provider=provider,subject=str(subject),email=email.lower() if email else None,display_name=name,avatar_url=avatar));db.commit();db.refresh(user);return user
@router.get('/auth/google')
def google_start(request:Request):
    if not providers()['google']:raise HTTPException(404)
    session=session_data(request);state=sign_oauth_state('google',session.get('uid') if session else None,session.get('csrf') if session else None);redirect=f"{s.web_base_url.rstrip('/')}/auth/google/callback"
    query=urlencode({'client_id':s.google_client_id,'redirect_uri':redirect,'response_type':'code','scope':'openid email profile','state':state,'access_type':'online','prompt':'select_account'})
    return RedirectResponse(f'https://accounts.google.com/o/oauth2/v2/auth?{query}')
@router.get('/auth/google/callback')
async def google_callback(request:Request,code:str,state:str,db:Session=Depends(get_db)):
    parsed=read_oauth_state(state,'google')
    if not parsed:raise HTTPException(401,'Invalid or expired OAuth state')
    session=session_data(request)
    if parsed.get('uid') and (not session or session.get('uid')!=parsed['uid'] or session.get('csrf')!=parsed.get('bind')):raise HTTPException(401,'Account-linking session changed')
    redirect=f"{s.web_base_url.rstrip('/')}/auth/google/callback"
    async with httpx.AsyncClient(timeout=20) as c:
        token=await c.post('https://oauth2.googleapis.com/token',data={'code':code,'client_id':s.google_client_id,'client_secret':s.google_client_secret,'redirect_uri':redirect,'grant_type':'authorization_code'});token.raise_for_status();access=token.json().get('access_token')
        info=await c.get('https://openidconnect.googleapis.com/v1/userinfo',headers={'Authorization':f'Bearer {access}'});info.raise_for_status();profile=info.json()
    if not profile.get('email_verified'):raise HTTPException(401,'Google email is not verified')
    user=identity_login(db,'google',profile['sub'],profile.get('email'),profile.get('name'),profile.get('picture'),parsed.get('uid'));return login_response(user.id)
@router.get('/auth/github')
def github_start(request:Request):
    if not providers()['github']:raise HTTPException(404)
    session=session_data(request);state=sign_oauth_state('github',session.get('uid') if session else None,session.get('csrf') if session else None);redirect=f"{s.web_base_url.rstrip('/')}/auth/github/callback"
    return RedirectResponse('https://github.com/login/oauth/authorize?'+urlencode({'client_id':s.github_client_id,'redirect_uri':redirect,'scope':'read:user user:email','state':state}))
@router.get('/auth/github/callback')
async def github_callback(request:Request,code:str,state:str,db:Session=Depends(get_db)):
    parsed=read_oauth_state(state,'github')
    if not parsed:raise HTTPException(401,'Invalid or expired OAuth state')
    session=session_data(request)
    if parsed.get('uid') and (not session or session.get('uid')!=parsed['uid'] or session.get('csrf')!=parsed.get('bind')):raise HTTPException(401,'Account-linking session changed')
    redirect=f"{s.web_base_url.rstrip('/')}/auth/github/callback"
    async with httpx.AsyncClient(timeout=20,headers={'Accept':'application/json','User-Agent':'BlazeNXT-v1'}) as c:
        token=await c.post('https://github.com/login/oauth/access_token',data={'client_id':s.github_client_id,'client_secret':s.github_client_secret,'code':code,'redirect_uri':redirect});token.raise_for_status();access=token.json().get('access_token');headers={'Authorization':f'Bearer {access}','Accept':'application/vnd.github+json'}
        info=await c.get('https://api.github.com/user',headers=headers);info.raise_for_status();profile=info.json();email=profile.get('email')
        if not email:
            emails=await c.get('https://api.github.com/user/emails',headers=headers);emails.raise_for_status();verified=[x for x in emails.json() if x.get('verified')];email=next((x['email'] for x in verified if x.get('primary')),verified[0]['email'] if verified else None)
    user=identity_login(db,'github',profile['id'],email,profile.get('name') or profile.get('login'),profile.get('avatar_url'),parsed.get('uid'));return login_response(user.id)
def send_magic_email(email,link):
    msg=EmailMessage();msg['Subject']='Your BlazeNXT v1 sign-in link';msg['From']=s.smtp_from;msg['To']=email;msg.set_content(f'Sign in to BlazeNXT v1:\n\n{link}\n\nThis link expires in {s.magic_link_ttl_seconds//60} minutes. If you did not request it, ignore this email.')
    with smtplib.SMTP(s.smtp_host,s.smtp_port,timeout=20) as server:
        if s.smtp_starttls:server.starttls()
        if s.smtp_username:server.login(s.smtp_username,s.smtp_password)
        server.send_message(msg)
@router.post('/auth/email',response_class=HTMLResponse)
async def email_start(request:Request,email:str=Form(...),token:str=Form('')):
    if not providers()['email']:raise HTTPException(404)
    email=email.strip().lower()
    if len(email)>320 or not EMAIL.fullmatch(email):raise HTTPException(400,'Invalid email address')
    session=session_data(request);link_uid=None
    if session:
        if not token or token!=session.get('csrf'):raise HTTPException(403,'Invalid CSRF token')
        link_uid=session.get('uid')
    signed=sign_magic_link(email,link_uid,session.get('csrf') if session else None);link=f"{s.web_base_url.rstrip('/')}/auth/email/verify?token={signed}"
    await asyncio.to_thread(send_magic_email,email,link)
    return templates.TemplateResponse('magic_sent.html',{'request':request,'email_hint':email[:2]+'***@'+email.split('@')[1]})
@router.get('/auth/email/verify')
def email_verify(request:Request,token:str,db:Session=Depends(get_db)):
    parsed=read_magic_link(token)
    if not parsed:raise HTTPException(401,'Magic link is invalid or expired')
    session=session_data(request)
    if parsed.get('uid') and (not session or session.get('uid')!=parsed['uid'] or session.get('csrf')!=parsed.get('bind')):raise HTTPException(401,'Account-linking session changed')
    fingerprint=hashlib.sha256(parsed['nonce'].encode()).hexdigest()
    if db.scalar(select(AuthTokenUse).where(AuthTokenUse.token_hash==fingerprint)):raise HTTPException(401,'Magic link was already used')
    db.add(AuthTokenUse(token_hash=fingerprint));db.flush();email=parsed['email'];user=identity_login(db,'email',email,email,email.split('@')[0],None,parsed.get('uid'));return login_response(user.id)
@router.get('/account/security',response_class=HTMLResponse)
def account_security(request:Request,db:Session=Depends(get_db)):
    session=session_data(request)
    if not session:return RedirectResponse('/',303)
    user=db.get(User,int(session['uid']))
    if not user or user.banned:raise HTTPException(403)
    identities=db.scalars(select(AuthIdentity).where(AuthIdentity.user_id==user.id).order_by(AuthIdentity.created_at)).all()
    return templates.TemplateResponse('security.html',{'request':request,'user':user,'csrf':session['csrf'],'bot_username':s.bot_username,'bot_runtime':getattr(request.app.state,'bot_runtime',{'online':False}),'identities':identities,'providers':providers()})

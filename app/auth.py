import asyncio,hashlib,json,re,secrets,smtplib
import pyotp
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
from app.models import ApiKey,AuditLog,AuthIdentity,AuthIdentityBlock,AuthTokenUse,User,UserSecurity,UserSessionPolicy
from app.security import decrypt_secret,encrypt_secret,hash_token,read_magic_link,read_oauth_state,read_preauth,read_session,sign_magic_link,sign_oauth_state,sign_preauth,sign_session
s=get_settings();router=APIRouter();templates=Jinja2Templates(directory='templates');EMAIL=re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
def providers():return {'google':bool(s.google_client_id and s.google_client_secret),'github':bool(s.github_client_id and s.github_client_secret),'email':bool(s.smtp_host and s.smtp_from)}
def session_data(request,db=None):
    session=read_session(request.cookies.get('blaze_session',''))
    if session and db:
        policy=db.scalar(select(UserSessionPolicy).where(UserSessionPolicy.user_id==int(session['uid'])))
        if policy:
            revoked=policy.revoked_before
            if revoked.tzinfo is None:revoked=revoked.replace(tzinfo=timezone.utc)
            if float(session.get('iat',0))<=revoked.timestamp():return None
    return session
def synthetic_telegram_id(provider,subject):return -1-int.from_bytes(hashlib.sha256(f'{provider}:{subject}'.encode()).digest()[:7],'big')
def login_response(uid,db,provider='unknown'):
    security=db.scalar(select(UserSecurity).where(UserSecurity.user_id==uid,UserSecurity.enabled==True))
    if security:
        r=RedirectResponse('/auth/2fa',303);r.set_cookie('blaze_preauth',sign_preauth(uid),httponly=True,secure=s.production,samesite='lax',max_age=300);r.set_cookie('blaze_preauth_provider',provider,httponly=True,secure=s.production,samesite='lax',max_age=300);return r
    r=RedirectResponse('/dashboard',303);r.set_cookie('blaze_session',sign_session(uid,provider),httponly=True,secure=s.production,samesite='lax',max_age=s.session_ttl_seconds);return r
def identity_login(db,provider,subject,email,name,avatar=None,link_uid=None):
    identity=db.scalar(select(AuthIdentity).where(AuthIdentity.provider==provider,AuthIdentity.subject==str(subject)))
    if identity:
        identity.last_login_at=datetime.now(timezone.utc);identity.email=email or identity.email;identity.display_name=name or identity.display_name;user=db.get(User,identity.user_id);db.commit();return user
    subject=str(subject);blocked=db.scalars(select(AuthIdentityBlock).where(AuthIdentityBlock.provider==provider,AuthIdentityBlock.subject==subject)).all();user=db.get(User,int(link_uid)) if link_uid else None
    if user:
        for block in blocked:
            if block.user_id==user.id:db.delete(block)
    if not user and email and not blocked:user=db.scalar(select(User).join(AuthIdentity,AuthIdentity.user_id==User.id).where(AuthIdentity.email==email.lower()))
    if not user:
        sid=synthetic_telegram_id(provider,str(subject))
        while db.scalar(select(User).where(User.telegram_id==sid)):sid-=1
        user=User(telegram_id=sid,username=(email.split('@')[0][:64] if email else f'{provider}_{subject}'[:64]),display_name=(name or email or f'{provider.title()} User')[:128]);db.add(user);db.flush()
    db.add(AuthIdentity(user_id=user.id,provider=provider,subject=str(subject),email=email.lower() if email else None,display_name=name,avatar_url=avatar));db.commit();db.refresh(user);return user
@router.get('/auth/google')
def google_start(request:Request,db:Session=Depends(get_db)):
    if not providers()['google']:raise HTTPException(404)
    session=session_data(request,db);state=sign_oauth_state('google',session.get('uid') if session else None,session.get('csrf') if session else None);redirect=f"{s.web_base_url.rstrip('/')}/auth/google/callback"
    query=urlencode({'client_id':s.google_client_id,'redirect_uri':redirect,'response_type':'code','scope':'openid email profile','state':state,'access_type':'online','prompt':'select_account'})
    return RedirectResponse(f'https://accounts.google.com/o/oauth2/v2/auth?{query}')
@router.get('/auth/google/callback')
async def google_callback(request:Request,code:str,state:str,db:Session=Depends(get_db)):
    parsed=read_oauth_state(state,'google')
    if not parsed:raise HTTPException(401,'Invalid or expired OAuth state')
    session=session_data(request,db)
    if parsed.get('uid') and (not session or session.get('uid')!=parsed['uid'] or session.get('csrf')!=parsed.get('bind')):raise HTTPException(401,'Account-linking session changed')
    redirect=f"{s.web_base_url.rstrip('/')}/auth/google/callback"
    async with httpx.AsyncClient(timeout=20) as c:
        token=await c.post('https://oauth2.googleapis.com/token',data={'code':code,'client_id':s.google_client_id,'client_secret':s.google_client_secret,'redirect_uri':redirect,'grant_type':'authorization_code'});token.raise_for_status();access=token.json().get('access_token')
        info=await c.get('https://openidconnect.googleapis.com/v1/userinfo',headers={'Authorization':f'Bearer {access}'});info.raise_for_status();profile=info.json()
    if not profile.get('email_verified'):raise HTTPException(401,'Google email is not verified')
    user=identity_login(db,'google',profile['sub'],profile.get('email'),profile.get('name'),profile.get('picture'),parsed.get('uid'));return login_response(user.id,db,'google')
@router.get('/auth/github')
def github_start(request:Request,db:Session=Depends(get_db)):
    if not providers()['github']:raise HTTPException(404)
    session=session_data(request,db);state=sign_oauth_state('github',session.get('uid') if session else None,session.get('csrf') if session else None);redirect=f"{s.web_base_url.rstrip('/')}/auth/github/callback"
    return RedirectResponse('https://github.com/login/oauth/authorize?'+urlencode({'client_id':s.github_client_id,'redirect_uri':redirect,'scope':'read:user user:email','state':state}))
@router.get('/auth/github/callback')
async def github_callback(request:Request,code:str,state:str,db:Session=Depends(get_db)):
    parsed=read_oauth_state(state,'github')
    if not parsed:raise HTTPException(401,'Invalid or expired OAuth state')
    session=session_data(request,db)
    if parsed.get('uid') and (not session or session.get('uid')!=parsed['uid'] or session.get('csrf')!=parsed.get('bind')):raise HTTPException(401,'Account-linking session changed')
    redirect=f"{s.web_base_url.rstrip('/')}/auth/github/callback"
    async with httpx.AsyncClient(timeout=20,headers={'Accept':'application/json','User-Agent':'BlazeNXT-v1'}) as c:
        token=await c.post('https://github.com/login/oauth/access_token',data={'client_id':s.github_client_id,'client_secret':s.github_client_secret,'code':code,'redirect_uri':redirect});token.raise_for_status();access=token.json().get('access_token');headers={'Authorization':f'Bearer {access}','Accept':'application/vnd.github+json'}
        info=await c.get('https://api.github.com/user',headers=headers);info.raise_for_status();profile=info.json();email=profile.get('email')
        if not email:
            emails=await c.get('https://api.github.com/user/emails',headers=headers);emails.raise_for_status();verified=[x for x in emails.json() if x.get('verified')];email=next((x['email'] for x in verified if x.get('primary')),verified[0]['email'] if verified else None)
    user=identity_login(db,'github',profile['id'],email,profile.get('name') or profile.get('login'),profile.get('avatar_url'),parsed.get('uid'));return login_response(user.id,db,'github')
def send_magic_email(email,link):
    msg=EmailMessage();msg['Subject']='Your BlazeNXT v1 sign-in link';msg['From']=s.smtp_from;msg['To']=email;msg.set_content(f'Sign in to BlazeNXT v1:\n\n{link}\n\nThis link expires in {s.magic_link_ttl_seconds//60} minutes. If you did not request it, ignore this email.')
    with smtplib.SMTP(s.smtp_host,s.smtp_port,timeout=20) as server:
        if s.smtp_starttls:server.starttls()
        if s.smtp_username:server.login(s.smtp_username,s.smtp_password)
        server.send_message(msg)
@router.post('/auth/email',response_class=HTMLResponse)
async def email_start(request:Request,email:str=Form(...),token:str=Form(''),db:Session=Depends(get_db)):
    if not providers()['email']:raise HTTPException(404)
    email=email.strip().lower()
    if len(email)>320 or not EMAIL.fullmatch(email):raise HTTPException(400,'Invalid email address')
    session=session_data(request,db);link_uid=None
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
    session=session_data(request,db)
    if parsed.get('uid') and (not session or session.get('uid')!=parsed['uid'] or session.get('csrf')!=parsed.get('bind')):raise HTTPException(401,'Account-linking session changed')
    fingerprint=hashlib.sha256(parsed['nonce'].encode()).hexdigest()
    if db.scalar(select(AuthTokenUse).where(AuthTokenUse.token_hash==fingerprint)):raise HTTPException(401,'Magic link was already used')
    db.add(AuthTokenUse(token_hash=fingerprint));db.flush();email=parsed['email'];user=identity_login(db,'email',email,email,email.split('@')[0],None,parsed.get('uid'));return login_response(user.id,db,'email')
@router.get('/account/security',response_class=HTMLResponse)
def account_security(request:Request,db:Session=Depends(get_db)):
    session=session_data(request,db)
    if not session:return RedirectResponse('/',303)
    user=db.get(User,int(session['uid']))
    if not user or user.banned:raise HTTPException(403)
    identities=db.scalars(select(AuthIdentity).where(AuthIdentity.user_id==user.id).order_by(AuthIdentity.created_at)).all();keys=db.scalars(select(ApiKey).where(ApiKey.user_id==user.id,ApiKey.revoked==False).order_by(ApiKey.created_at.desc())).all();security=db.scalar(select(UserSecurity).where(UserSecurity.user_id==user.id))
    return templates.TemplateResponse('security.html',{'request':request,'user':user,'csrf':session['csrf'],'bot_username':s.bot_username,'bot_runtime':getattr(request.app.state,'bot_runtime',{'online':False}),'identities':identities,'linked_providers':{i.provider for i in identities},'providers':providers(),'api_keys':keys,'two_factor':bool(security and security.enabled),'current_provider':session.get('provider','unknown')})
@router.post('/account/identities/{identity_id}/unlink')
def unlink_identity(identity_id:int,request:Request,confirmation:str=Form(...),code:str=Form(''),token:str=Form(...),db:Session=Depends(get_db)):
    session=session_data(request,db)
    if not session or token!=session.get('csrf'):raise HTTPException(403,'Invalid CSRF token')
    if confirmation.strip().upper()!='UNLINK':raise HTTPException(400,'Type UNLINK to confirm')
    uid=int(session['uid']);identity=db.get(AuthIdentity,identity_id)
    if not identity or identity.user_id!=uid:raise HTTPException(404)
    identities=db.scalars(select(AuthIdentity).where(AuthIdentity.user_id==uid)).all()
    if len(identities)<=1:raise HTTPException(400,'Add another login method before unlinking the last identity')
    security=db.scalar(select(UserSecurity).where(UserSecurity.user_id==uid,UserSecurity.enabled==True))
    if security and not pyotp.TOTP(decrypt_secret(security.encrypted_totp_secret)).verify(code.replace(' ',''),valid_window=1):raise HTTPException(401,'Valid authenticator code required')
    user=db.get(User,uid);db.add(AuthIdentityBlock(provider=identity.provider,subject=identity.subject,user_id=uid))
    if identity.provider=='telegram' and user.telegram_id>0:
        replacement=synthetic_telegram_id('unlinked',f'{uid}:{secrets.token_urlsafe(8)}')
        while db.scalar(select(User).where(User.telegram_id==replacement)):replacement-=1
        user.telegram_id=replacement
    provider=identity.provider;db.delete(identity);policy=db.scalar(select(UserSessionPolicy).where(UserSessionPolicy.user_id==uid))
    if not policy:policy=UserSessionPolicy(user_id=uid,revoked_before=datetime.now(timezone.utc));db.add(policy)
    else:policy.revoked_before=datetime.now(timezone.utc)
    db.add(AuditLog(actor_id=uid,action='identity.unlink',target=f'identity:{provider}',ip=request.client.host if request.client else 'unknown',detail=json.dumps({'provider':provider})));db.commit();response=RedirectResponse('/?unlinked=1',303);response.delete_cookie('blaze_session');return response
@router.post('/account/api-keys',response_class=HTMLResponse)
def create_api_key(request:Request,name:str=Form(...),scope_set:str=Form('read'),token:str=Form(...),db:Session=Depends(get_db)):
    session=session_data(request,db)
    if not session or token!=session.get('csrf'):raise HTTPException(403,'Invalid CSRF token')
    user=db.get(User,int(session['uid']))
    if not user or user.banned:raise HTTPException(403)
    scopes={'read':['servers:read'],'control':['servers:read','servers:control'],'full':['*']}.get(scope_set)
    if not scopes:raise HTTPException(400,'Invalid scope set')
    raw=f"blz_{secrets.token_urlsafe(8)}_{secrets.token_urlsafe(32)}";db.add(ApiKey(user_id=user.id,name=name[:80],prefix=raw[:16],key_hash=hash_token(raw),scopes=json.dumps(scopes)));db.commit()
    return templates.TemplateResponse('api_key_created.html',{'request':request,'api_key':raw})
@router.post('/account/api-keys/{key_id}/revoke')
def revoke_api_key(key_id:int,request:Request,token:str=Form(...),db:Session=Depends(get_db)):
    session=session_data(request,db)
    if not session or token!=session.get('csrf'):raise HTTPException(403)
    key=db.get(ApiKey,key_id)
    if not key or key.user_id!=int(session['uid']):raise HTTPException(404)
    key.revoked=True;db.commit();return RedirectResponse('/account/security',303)
@router.get('/account/2fa/setup',response_class=HTMLResponse)
def setup_2fa(request:Request,db:Session=Depends(get_db)):
    session=session_data(request,db)
    if not session:return RedirectResponse('/',303)
    uid=int(session['uid']);row=db.scalar(select(UserSecurity).where(UserSecurity.user_id==uid));secret=pyotp.random_base32();codes=['-'.join([secrets.token_hex(3),secrets.token_hex(3)]) for _ in range(8)];hashed=[hash_token(x) for x in codes]
    if row and row.enabled:return RedirectResponse('/account/security',303)
    if row:row.encrypted_totp_secret=encrypt_secret(secret);row.encrypted_recovery_codes=encrypt_secret(json.dumps(hashed))
    else:db.add(UserSecurity(user_id=uid,encrypted_totp_secret=encrypt_secret(secret),encrypted_recovery_codes=encrypt_secret(json.dumps(hashed))))
    db.commit();user=db.get(User,uid);uri=pyotp.TOTP(secret).provisioning_uri(name=user.display_name,issuer_name='BlazeNXT v1')
    return templates.TemplateResponse('two_factor_setup.html',{'request':request,'user':user,'csrf':session['csrf'],'secret':secret,'uri':uri,'recovery_codes':codes,'bot_username':s.bot_username,'bot_runtime':getattr(request.app.state,'bot_runtime',{'online':False})})
@router.post('/account/2fa/enable')
def enable_2fa(request:Request,code:str=Form(...),token:str=Form(...),db:Session=Depends(get_db)):
    session=session_data(request,db)
    if not session or token!=session.get('csrf'):raise HTTPException(403)
    row=db.scalar(select(UserSecurity).where(UserSecurity.user_id==int(session['uid'])))
    if not row or not pyotp.TOTP(decrypt_secret(row.encrypted_totp_secret)).verify(code,valid_window=1):raise HTTPException(400,'Invalid authenticator code')
    row.enabled=True;db.commit();return RedirectResponse('/account/security',303)
@router.post('/account/2fa/disable')
def disable_2fa(request:Request,code:str=Form(...),token:str=Form(...),db:Session=Depends(get_db)):
    session=session_data(request,db)
    if not session or token!=session.get('csrf'):raise HTTPException(403)
    row=db.scalar(select(UserSecurity).where(UserSecurity.user_id==int(session['uid'])))
    if not row or not pyotp.TOTP(decrypt_secret(row.encrypted_totp_secret)).verify(code,valid_window=1):raise HTTPException(400,'Invalid authenticator code')
    db.delete(row);db.commit();return RedirectResponse('/account/security',303)
@router.get('/auth/2fa',response_class=HTMLResponse)
def two_factor_challenge(request:Request):
    if not read_preauth(request.cookies.get('blaze_preauth','')):return RedirectResponse('/',303)
    return templates.TemplateResponse('two_factor_challenge.html',{'request':request})
@router.post('/auth/2fa')
def verify_two_factor(request:Request,code:str=Form(...),db:Session=Depends(get_db)):
    preauth=read_preauth(request.cookies.get('blaze_preauth',''))
    if not preauth:raise HTTPException(401,'Login challenge expired')
    uid=int(preauth['uid']);row=db.scalar(select(UserSecurity).where(UserSecurity.user_id==uid,UserSecurity.enabled==True))
    if not row:raise HTTPException(401,'Two-factor authentication is unavailable')
    valid=pyotp.TOTP(decrypt_secret(row.encrypted_totp_secret)).verify(code.replace(' ',''),valid_window=1)
    if not valid:
        recovery=json.loads(decrypt_secret(row.encrypted_recovery_codes));fingerprint=hash_token(code.strip())
        if fingerprint in recovery:recovery.remove(fingerprint);row.encrypted_recovery_codes=encrypt_secret(json.dumps(recovery));db.commit();valid=True
    if not valid:raise HTTPException(401,'Invalid authenticator or recovery code')
    provider=request.cookies.get('blaze_preauth_provider','unknown');response=RedirectResponse('/dashboard',303);response.delete_cookie('blaze_preauth');response.delete_cookie('blaze_preauth_provider');response.set_cookie('blaze_session',sign_session(uid,provider),httponly=True,secure=s.production,samesite='lax',max_age=s.session_ttl_seconds);return response

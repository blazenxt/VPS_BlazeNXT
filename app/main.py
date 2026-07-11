import hashlib,io,logging,re,secrets,time,zipfile
from contextlib import asynccontextmanager
from datetime import datetime,timezone
from fastapi import BackgroundTasks,Depends,FastAPI,File,Form,HTTPException,Request,Response,UploadFile
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST,Counter,generate_latest
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from app.config import get_settings
from app.db import Base,SessionLocal,engine,get_db
from app.models import Artifact,AuditLog,Role,RunnerToken,State,User,Workload
from app.railway import RailwayClient
from app.security import hash_token,inspect_zip,read_session,safe_filename,sign_session,verify_telegram
from app.services import audit,provision,quota
s=get_settings();templates=Jinja2Templates(directory='templates');REQ=Counter('blaze_http_requests_total','HTTP requests',['method','path','status']);rate={};logger=logging.getLogger('blazenxt')
async def configure_telegram_webhook():
    import httpx
    webhook=f"{s.web_base_url.rstrip('/')}/telegram/webhook/{s.telegram_webhook_secret}"
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.post(f'https://api.telegram.org/bot{s.bot_token}/setWebhook',json={'url':webhook,'drop_pending_updates':False,'allowed_updates':['message']})
    response.raise_for_status();result=response.json()
    if not result.get('ok'):raise RuntimeError(result.get('description','Telegram rejected webhook'))
    logger.info('Telegram webhook configured for %s',s.web_base_url)
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    if s.production and ('change-me' in s.app_secret or not s.bot_token):raise RuntimeError('Production secrets are not configured')
    if s.bot_token and s.web_base_url.startswith('https://') and s.telegram_webhook_secret!='change-me':
        try:await configure_telegram_webhook()
        except Exception:logger.exception('Automatic Telegram webhook configuration failed')
    yield
app=FastAPI(title='BlazeNXT Hosting',version='2.0.0',docs_url=None if s.production else '/docs',lifespan=lifespan)
app.mount('/static',StaticFiles(directory='static'),name='static')
@app.middleware('http')
async def security_headers(request,call_next):
    ip=request.client.host if request.client else 'unknown';key=f'{ip}:{request.url.path}';now=time.time();hits=[x for x in rate.get(key,[]) if now-x<60];limit=20 if request.url.path.startswith(('/auth','/api')) else 120
    if len(hits)>=limit:return JSONResponse({'detail':'rate limit exceeded'},429)
    hits.append(now);rate[key]=hits;response=await call_next(request)
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'strict-origin-when-cross-origin','Permissions-Policy':'camera=(), microphone=(), geolocation=()','Content-Security-Policy':"default-src 'self'; img-src 'self' data: https://t.me; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; frame-src https://oauth.telegram.org",'Strict-Transport-Security':'max-age=31536000; includeSubDomains'})
    REQ.labels(request.method,request.url.path,response.status_code).inc();return response
def current(request:Request,db:Session=Depends(get_db)):
    p=read_session(request.cookies.get('blaze_session',''))
    if not p:raise HTTPException(401,'Sign in required')
    u=db.get(User,int(p['uid']))
    if not u or u.banned:raise HTTPException(403,'Account unavailable')
    request.state.session=p;return u
def csrf(request:Request,token:str=Form(...)):
    p=getattr(request.state,'session',None) or read_session(request.cookies.get('blaze_session',''))
    if not p or not secrets.compare_digest(p.get('csrf',''),token):raise HTTPException(403,'Invalid CSRF token')
def ctx(request,user=None,**extra):
    p=read_session(request.cookies.get('blaze_session','')) or {};return {'request':request,'user':user,'csrf':p.get('csrf',''),'bot_username':s.bot_username,**extra}
@app.get('/',response_class=HTMLResponse)
def home(request:Request):return templates.TemplateResponse('home.html',ctx(request))
@app.get('/auth/telegram')
def auth(request:Request,db:Session=Depends(get_db)):
    data=dict(request.query_params)
    if not s.bot_token or not verify_telegram(data):raise HTTPException(401,'Invalid or expired Telegram login')
    tid=int(data['id']);u=db.scalar(select(User).where(User.telegram_id==tid));name=' '.join(filter(None,[data.get('first_name'),data.get('last_name')])) or 'User'
    if not u:u=User(telegram_id=tid,username=data.get('username'),display_name=name,role=Role.owner if tid in s.owners else Role.user);db.add(u)
    else:u.username=data.get('username');u.display_name=name;u.role=Role.owner if tid in s.owners else u.role
    db.commit();db.refresh(u);audit(db,u,'login','web',request.client.host if request.client else '');db.commit();r=RedirectResponse('/dashboard',303);r.set_cookie('blaze_session',sign_session(u.id),httponly=True,secure=s.production,samesite='lax',max_age=s.session_ttl_seconds);return r
@app.post('/logout')
def logout(request:Request,u=Depends(current),_=Depends(csrf)):
    r=RedirectResponse('/',303);r.delete_cookie('blaze_session');return r
@app.get('/dashboard',response_class=HTMLResponse)
def dashboard(request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    ws=db.scalars(select(Workload).where(Workload.user_id==u.id,Workload.state!=State.deleted).order_by(Workload.created_at.desc())).all();return templates.TemplateResponse('dashboard.html',ctx(request,u,workloads=ws,quota=quota(u)))
@app.post('/workloads')
async def upload(request:Request,bg:BackgroundTasks,name:str=Form(...),runtime:str=Form(...),entrypoint:str=Form(...),file:UploadFile=File(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    active=db.scalar(select(func.count()).select_from(Workload).where(Workload.user_id==u.id,Workload.state!=State.deleted)) or 0
    if active>=quota(u):raise HTTPException(403,'Workload quota reached')
    if runtime not in {'python','node'} or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{1,79}',name):raise HTTPException(400,'Invalid configuration')
    try:filename=safe_filename(file.filename or 'upload');entrypoint=safe_filename(entrypoint)
    except ValueError as e:raise HTTPException(400,str(e))
    data=await file.read(s.max_upload_mb*1024*1024+1)
    if len(data)>s.max_upload_mb*1024*1024:raise HTTPException(413,'Upload too large')
    if filename.endswith('.zip'):
        try:inspect_zip(data)
        except Exception as e:raise HTTPException(400,str(e))
    elif not ((runtime=='python' and filename.endswith('.py')) or (runtime=='node' and filename.endswith('.js'))):raise HTTPException(400,'Upload matching script or ZIP')
    a=Artifact(owner_id=u.id,filename=filename,content_type=file.content_type or 'application/octet-stream',sha256=hashlib.sha256(data).hexdigest(),size=len(data),data=data);db.add(a);db.flush();w=Workload(user_id=u.id,artifact_id=a.id,name=name,runtime=runtime,entrypoint=entrypoint);db.add(w);db.flush();audit(db,u,'workload.create',f'workload:{w.id}',request.client.host if request.client else '',{'sha256':a.sha256});db.commit();bg.add_task(run_provision,w.id);return RedirectResponse('/dashboard',303)
async def run_provision(wid):
    with SessionLocal() as db:
        w=db.get(Workload,wid)
        if w:await provision(db,w)
@app.post('/workloads/{wid}/{action}')
async def action(wid:int,action:str,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=db.get(Workload,wid)
    if not w or (w.user_id!=u.id and u.role not in {Role.admin,Role.owner}):raise HTTPException(404)
    c=RailwayClient()
    try:
        if action in {'start','restart'}:await c.redeploy(w.railway_service_id);w.state=State.running
        elif action=='stop':
            ds=await c.deployments(w.railway_service_id)
            if ds:await c.stop(ds[0]['id'])
            w.state=State.stopped
        elif action=='delete':await c.delete(w.railway_service_id);w.state=State.deleted
        else:raise HTTPException(400,'Unknown action')
        audit(db,u,f'workload.{action}',f'workload:{wid}',request.client.host if request.client else '');db.commit()
    except HTTPException:raise
    except Exception as e:w.last_error=str(e)[:1000];db.commit();raise HTTPException(502,str(e))
    return RedirectResponse(f'/servers/{wid}',303)
@app.get('/api/workloads/{wid}/logs')
async def logs(wid:int,u:User=Depends(current),db:Session=Depends(get_db)):
    w=db.get(Workload,wid)
    if not w or (w.user_id!=u.id and u.role not in {Role.admin,Role.owner}):raise HTTPException(404)
    ds=await RailwayClient().deployments(w.railway_service_id);return {'logs':await RailwayClient().logs(ds[0]['id']) if ds else []}
def owned_workload(wid:int,u:User,db:Session):
    w=db.get(Workload,wid)
    if not w or (w.user_id!=u.id and u.role not in {Role.admin,Role.owner}):raise HTTPException(404)
    return w

def artifact_files(a:Artifact):
    if not a.filename.endswith('.zip'):return [{'name':a.filename,'size':a.size,'kind':'file'}]
    try:
        with zipfile.ZipFile(io.BytesIO(a.data)) as z:
            return [{'name':x.filename,'size':x.file_size,'kind':'folder' if x.is_dir() else 'file'} for x in z.infolist()[:300]]
    except zipfile.BadZipFile:return []

@app.get('/servers/{wid}',response_class=HTMLResponse)
async def server_detail(wid:int,request:Request,tab:str='console',u:User=Depends(current),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);deployments=[];provider_error=None
    if w.railway_service_id:
        try:deployments=await RailwayClient().deployments(w.railway_service_id)
        except Exception as e:provider_error=str(e)
    events=db.scalars(select(AuditLog).where(AuditLog.target==f'workload:{wid}').order_by(AuditLog.created_at.desc()).limit(100)).all()
    return templates.TemplateResponse('server.html',ctx(request,u,w=w,tab=tab,deployments=deployments,provider_error=provider_error,files=artifact_files(w.artifact),events=events))

@app.get('/servers/{wid}/download')
def download_artifact(wid:int,u:User=Depends(current),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);name=re.sub(r'[^A-Za-z0-9_.-]','_',w.artifact.filename)
    return Response(w.artifact.data,media_type=w.artifact.content_type,headers={'Content-Disposition':f'attachment; filename="{name}"','X-Content-Type-Options':'nosniff'})

@app.get('/api/workloads/{wid}/status')
async def workload_status(wid:int,u:User=Depends(current),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);deployments=[]
    if w.railway_service_id:
        try:deployments=await RailwayClient().deployments(w.railway_service_id)
        except Exception as e:return {'state':w.state.value,'provider_error':str(e),'deployments':[]}
    return {'state':w.state.value,'service_id':w.railway_service_id,'deployments':deployments}

@app.get('/internal/artifacts/{wid}')
def artifact(wid:int,request:Request,db:Session=Depends(get_db)):
    token=request.headers.get('Authorization','').removeprefix('Bearer ');rt=db.scalar(select(RunnerToken).where(RunnerToken.workload_id==wid,RunnerToken.token_hash==hash_token(token))) if token else None;now=datetime.now(timezone.utc)
    if not rt or rt.expires_at<now:raise HTTPException(401,'Invalid runner token')
    w=db.get(Workload,wid);return Response(w.artifact.data,media_type=w.artifact.content_type,headers={'X-Filename':w.artifact.filename,'X-Content-SHA256':w.artifact.sha256,'Cache-Control':'no-store'})
@app.post('/telegram/webhook/{secret}')
async def telegram(secret:str,request:Request):
    if not secrets.compare_digest(secret,s.telegram_webhook_secret):raise HTTPException(404)
    update=await request.json();message=update.get('message',{});chat=message.get('chat',{});text=message.get('text','')
    if chat and text.startswith('/start') and s.bot_token:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:await c.post(f'https://api.telegram.org/bot{s.bot_token}/sendMessage',json={'chat_id':chat['id'],'text':'Welcome to BlazeNXT secure hosting.','reply_markup':{'inline_keyboard':[[{'text':'Open Dashboard','url':s.web_base_url}]]}})
    return {'ok':True}
@app.get('/admin',response_class=HTMLResponse)
def admin(request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner}:raise HTTPException(403)
    users=db.scalars(select(User).order_by(User.created_at.desc()).limit(200)).all();audits=db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).all();return templates.TemplateResponse('admin.html',ctx(request,u,users=users,audits=audits))
@app.post('/admin/users/{uid}')
def update_user(uid:int,request:Request,role:str=Form(...),banned:bool=Form(False),quota_value:str=Form(''),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if u.role!=Role.owner:raise HTTPException(403)
    t=db.get(User,uid)
    if not t:raise HTTPException(404)
    t.role=Role(role);t.banned=banned;t.quota=int(quota_value) if quota_value.strip() else None;audit(db,u,'user.update',f'user:{uid}',request.client.host if request.client else '');db.commit();return RedirectResponse('/admin',303)
@app.get('/health/live')
def live():return {'status':'ok'}
@app.get('/health/ready')
def ready(db:Session=Depends(get_db)):db.execute(select(1));return {'status':'ready','railway_configured':RailwayClient().configured}
@app.get('/metrics')
def metrics():return Response(generate_latest(),media_type=CONTENT_TYPE_LATEST)

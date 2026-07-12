import asyncio,hashlib,io,json,logging,re,secrets,time,zipfile
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from pathlib import PurePosixPath
from fastapi import BackgroundTasks,Depends,FastAPI,File,Form,HTTPException,Request,Response,UploadFile
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST,Counter,generate_latest
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from app.config import get_settings
from app.db import Base,SessionLocal,engine,get_db
from app.models import Artifact,AuditLog,Backup,Notification,Role,RunnerToken,Schedule,State,SupportTicket,User,Workload,WorkloadMember,WorkloadVariable
from app.railway import RailwayClient
from app.security import encrypt_secret,hash_token,inspect_zip,read_session,safe_filename,sign_session,verify_telegram
from app.services import audit,perform_action,provision,quota,refresh_artifact
s=get_settings();templates=Jinja2Templates(directory='templates');REQ=Counter('blaze_http_requests_total','HTTP requests',['method','path','status']);rate={};logger=logging.getLogger('blazenxt');BOT_RUNTIME={'online':False,'username':None,'id':None,'webhook':None,'error':None,'started_at':None}
async def configure_telegram_webhook():
    import httpx
    webhook=f"{s.web_base_url.rstrip('/')}/telegram/webhook/{s.telegram_webhook_secret}"
    async with httpx.AsyncClient(timeout=15) as client:
        identity=await client.post(f'https://api.telegram.org/bot{s.bot_token}/getMe');identity.raise_for_status();bot=identity.json()
        if not bot.get('ok'):raise RuntimeError(bot.get('description','Telegram identity rejected'))
        response=await client.post(f'https://api.telegram.org/bot{s.bot_token}/setWebhook',json={'url':webhook,'drop_pending_updates':False,'allowed_updates':['message','callback_query']});response.raise_for_status();result=response.json()
        if not result.get('ok'):raise RuntimeError(result.get('description','Telegram rejected webhook'))
        commands=await client.post(f'https://api.telegram.org/bot{s.bot_token}/setMyCommands',json={'commands':[{'command':'start','description':'Open BlazeNXT control center'},{'command':'servers','description':'List and control workloads'},{'command':'status','description':'Platform and account status'},{'command':'deploy','description':'Upload and deploy code'},{'command':'help','description':'Show deployment help'}]});commands.raise_for_status()
    info=bot['result'];BOT_RUNTIME.update({'online':True,'username':info.get('username'),'id':info.get('id'),'webhook':webhook,'error':None,'started_at':datetime.now(timezone.utc).isoformat()});logger.info('Telegram bot @%s started with webhook sync',info.get('username'))
async def schedule_worker():
    while True:
        await asyncio.sleep(30)
        try:
            with SessionLocal() as db:
                now=datetime.now(timezone.utc);rows=db.scalars(select(Schedule).where(Schedule.enabled==True,Schedule.next_run<=now)).all()
                for row in rows:
                    w=db.get(Workload,row.workload_id)
                    try:
                        if w and w.state!=State.deleted:await perform_action(db,w,row.action);audit(db,None,f'schedule.{row.action}',f'workload:{row.workload_id}','scheduler',{'schedule_id':row.id})
                    except Exception as e:audit(db,None,'schedule.failed',f'workload:{row.workload_id}','scheduler',{'error':str(e)[:300]})
                    row.last_run=now;row.next_run=now+timedelta(minutes=row.interval_minutes);db.commit()
        except Exception:logger.exception('Schedule worker failed')
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    if s.production and ('change-me' in s.app_secret or not s.bot_token):raise RuntimeError('Production secrets are not configured')
    if s.bot_token and s.web_base_url.startswith('https://') and s.telegram_webhook_secret!='change-me':
        try:await configure_telegram_webhook()
        except Exception as e:BOT_RUNTIME.update({'online':False,'error':str(e)[:300]});logger.exception('Automatic Telegram bot startup failed')
    else:BOT_RUNTIME.update({'online':False,'error':'Telegram variables or HTTPS base URL are incomplete'})
    scheduler=asyncio.create_task(schedule_worker())
    yield
    scheduler.cancel()
app=FastAPI(title='BlazeNXT Control Plane',version='1.0.0',docs_url=None if s.production else '/docs',lifespan=lifespan)
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
    p=read_session(request.cookies.get('blaze_session','')) or {};return {'request':request,'user':user,'csrf':p.get('csrf',''),'bot_username':s.bot_username,'bot_runtime':BOT_RUNTIME,**extra}
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
    shared_ids=db.scalars(select(WorkloadMember.workload_id).where(WorkloadMember.user_id==u.id)).all()
    ws=db.scalars(select(Workload).where((Workload.user_id==u.id)|(Workload.id.in_(shared_ids)),Workload.state!=State.deleted).order_by(Workload.created_at.desc())).all()
    notes=db.scalars(select(Notification).where(Notification.user_id==u.id).order_by(Notification.created_at.desc()).limit(5)).all()
    global_active=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0
    return templates.TemplateResponse('dashboard.html',ctx(request,u,workloads=ws,quota=quota(u),notifications=notes,global_active=global_active,global_limit=s.global_workload_limit))
@app.post('/workloads')
async def upload(request:Request,bg:BackgroundTasks,name:str=Form(...),runtime:str=Form(...),entrypoint:str=Form(...),file:UploadFile=File(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    active=db.scalar(select(func.count()).select_from(Workload).where(Workload.user_id==u.id,Workload.state!=State.deleted)) or 0
    global_active=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0
    if s.global_workload_limit and global_active>=s.global_workload_limit:raise HTTPException(503,'Platform capacity reached. An administrator must raise GLOBAL_WORKLOAD_LIMIT or upgrade Railway.')
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
    w=accessible_workload(wid,u,db,'control')
    if action=='delete' and w.user_id!=u.id and u.role not in {Role.admin,Role.owner}:raise HTTPException(403,'Only the owner can delete')
    try:
        await perform_action(db,w,action);audit(db,u,f'workload.{action}',f'workload:{wid}',request.client.host if request.client else '');db.commit()
        try:
            from app.telegram_bot import send_user_notification
            await send_user_notification(w.user_id,f'🔄 <b>{w.name}</b>: {action} completed from web panel.')
        except Exception:pass
    except Exception as e:w.last_error=str(e)[:1000];db.commit();raise HTTPException(502,str(e))
    return RedirectResponse(f'/servers/{wid}',303)
@app.get('/api/workloads/{wid}/logs')
async def logs(wid:int,u:User=Depends(current),db:Session=Depends(get_db)):
    w=accessible_workload(wid,u,db,'logs')
    if not w.railway_service_id:return {'logs':[]}
    ds=await RailwayClient().deployments(w.railway_service_id);return {'logs':await RailwayClient().logs(ds[0]['id']) if ds else []}
def accessible_workload(wid:int,u:User,db:Session,permission='view'):
    w=db.get(Workload,wid)
    if not w:raise HTTPException(404)
    if w.user_id==u.id or u.role in {Role.admin,Role.owner}:return w
    member=db.scalar(select(WorkloadMember).where(WorkloadMember.workload_id==wid,WorkloadMember.user_id==u.id))
    allowed=json.loads(member.permissions) if member else []
    if not member or permission not in allowed:raise HTTPException(404)
    return w
def owned_workload(wid:int,u:User,db:Session):
    w=accessible_workload(wid,u,db)
    if w.user_id!=u.id and u.role not in {Role.admin,Role.owner}:raise HTTPException(403,'Owner access required')
    return w

def artifact_files(a:Artifact):
    if not a.filename.endswith('.zip'):return [{'name':a.filename,'size':a.size,'kind':'file'}]
    try:
        with zipfile.ZipFile(io.BytesIO(a.data)) as z:
            return [{'name':x.filename,'size':x.file_size,'kind':'folder' if x.is_dir() else 'file'} for x in z.infolist()[:300]]
    except zipfile.BadZipFile:return []
def clean_archive_path(raw:str)->str:
    p=PurePosixPath(raw.replace('\\','/'))
    if not raw or p.is_absolute() or '..' in p.parts or len(str(p))>240:raise HTTPException(400,'Unsafe file path')
    return str(p)
def read_artifact_text(a:Artifact,path:str)->str:
    path=clean_archive_path(path)
    try:
        if a.filename.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(a.data)) as z:
                info=z.getinfo(path)
                if info.is_dir() or info.file_size>256*1024:raise HTTPException(400,'Only text files up to 256 KB can be edited')
                raw=z.read(info)
        else:
            if path!=a.filename or a.size>256*1024:raise HTTPException(404)
            raw=a.data
        return raw.decode('utf-8')
    except KeyError:raise HTTPException(404,'File not found')
    except UnicodeDecodeError:raise HTTPException(400,'Binary files cannot be edited')
def rebuild_artifact(a:Artifact,path:str,content:bytes|None,delete=False)->bytes:
    path=clean_archive_path(path)
    if not a.filename.endswith('.zip'):
        if path!=a.filename or delete:raise HTTPException(400,'Single entrypoint files cannot be deleted')
        return content or b''
    source=io.BytesIO(a.data);target=io.BytesIO();found=False
    with zipfile.ZipFile(source) as zin,zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename==path:
                found=True
                if delete:continue
                zout.writestr(item,content or b'')
            else:zout.writestr(item,zin.read(item.filename) if not item.is_dir() else b'')
        if not found and not delete:zout.writestr(path,content or b'')
    if delete and not found:raise HTTPException(404,'File not found')
    data=target.getvalue();inspect_zip(data);return data
async def apply_artifact_update(db,w,u,ip,data,event,detail=None):
    old=w.artifact;db.add(Backup(workload_id=w.id,name=f'Automatic backup before {event}',filename=old.filename,sha256=old.sha256,size=old.size,data=old.data))
    new=Artifact(owner_id=w.user_id,filename=old.filename,content_type=old.content_type,sha256=hashlib.sha256(data).hexdigest(),size=len(data),data=data);db.add(new);db.flush();w.artifact_id=new.id;audit(db,u,event,f'workload:{w.id}',ip,detail or {'sha256':new.sha256});db.commit()
    if w.railway_service_id:await refresh_artifact(db,w)

@app.get('/servers/{wid}',response_class=HTMLResponse)
async def server_detail(wid:int,request:Request,tab:str='console',u:User=Depends(current),db:Session=Depends(get_db)):
    w=accessible_workload(wid,u,db);deployments=[];provider_error=None
    if w.railway_service_id:
        try:deployments=await RailwayClient().deployments(w.railway_service_id)
        except Exception as e:provider_error=str(e)
    events=db.scalars(select(AuditLog).where(AuditLog.target==f'workload:{wid}').order_by(AuditLog.created_at.desc()).limit(100)).all()
    variables=db.scalars(select(WorkloadVariable).where(WorkloadVariable.workload_id==wid).order_by(WorkloadVariable.name)).all()
    backups=db.scalars(select(Backup).where(Backup.workload_id==wid).order_by(Backup.created_at.desc())).all()
    members=db.scalars(select(WorkloadMember).where(WorkloadMember.workload_id==wid)).all()
    schedules=db.scalars(select(Schedule).where(Schedule.workload_id==wid).order_by(Schedule.created_at.desc())).all()
    is_owner=w.user_id==u.id or u.role in {Role.admin,Role.owner}
    return templates.TemplateResponse('server.html',ctx(request,u,w=w,tab=tab,deployments=deployments,provider_error=provider_error,files=artifact_files(w.artifact),events=events,variables=variables,backups=backups,members=members,schedules=schedules,is_owner=is_owner))

@app.get('/servers/{wid}/download')
def download_artifact(wid:int,u:User=Depends(current),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);name=re.sub(r'[^A-Za-z0-9_.-]','_',w.artifact.filename)
    return Response(w.artifact.data,media_type=w.artifact.content_type,headers={'Content-Disposition':f'attachment; filename="{name}"','X-Content-Type-Options':'nosniff'})

@app.get('/servers/{wid}/files/edit',response_class=HTMLResponse)
def edit_file_page(wid:int,path:str,request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    w=accessible_workload(wid,u,db,'files');content=read_artifact_text(w.artifact,path);is_owner=w.user_id==u.id or u.role in {Role.admin,Role.owner}
    return templates.TemplateResponse('file_editor.html',ctx(request,u,w=w,path=clean_archive_path(path),content=content,is_owner=is_owner))
@app.post('/servers/{wid}/files/save')
async def save_artifact_file(wid:int,request:Request,path:str=Form(...),content:str=Form(''),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);raw=content.encode()
    if len(raw)>256*1024:raise HTTPException(413,'Editor limit is 256 KB')
    path=clean_archive_path(path);data=rebuild_artifact(w.artifact,path,raw);await apply_artifact_update(db,w,u,request.client.host if request.client else '',data,'file.save',{'path':path});return RedirectResponse(f'/servers/{wid}?tab=files',303)
@app.post('/servers/{wid}/files/delete')
async def delete_artifact_file(wid:int,request:Request,path:str=Form(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);path=clean_archive_path(path)
    if path==w.entrypoint:raise HTTPException(400,'Entrypoint cannot be deleted')
    data=rebuild_artifact(w.artifact,path,None,delete=True);await apply_artifact_update(db,w,u,request.client.host if request.client else '',data,'file.delete',{'path':path});return RedirectResponse(f'/servers/{wid}?tab=files',303)
@app.post('/servers/{wid}/backups/{bid}/restore')
async def restore_backup(wid:int,bid:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);backup=db.get(Backup,bid)
    if not backup or backup.workload_id!=wid:raise HTTPException(404)
    old=w.artifact;db.add(Backup(workload_id=wid,name='Automatic pre-restore backup',filename=old.filename,sha256=old.sha256,size=old.size,data=old.data));a=Artifact(owner_id=w.user_id,filename=backup.filename,content_type='application/octet-stream',sha256=backup.sha256,size=backup.size,data=backup.data);db.add(a);db.flush();w.artifact_id=a.id;audit(db,u,'backup.restore',f'workload:{wid}',request.client.host if request.client else '',{'backup_id':bid});db.commit()
    if w.railway_service_id:await refresh_artifact(db,w)
    return RedirectResponse(f'/servers/{wid}?tab=backups',303)
@app.post('/servers/{wid}/rename')
def rename_workload(wid:int,request:Request,name:str=Form(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{1,79}',name):raise HTTPException(400,'Invalid name')
    old=w.name;w.name=name;audit(db,u,'workload.rename',f'workload:{wid}',request.client.host if request.client else '',{'old':old,'new':name});db.commit();return RedirectResponse(f'/servers/{wid}?tab=settings',303)
@app.post('/servers/{wid}/reinstall')
async def reinstall_workload(wid:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db)
    if not w.railway_service_id:raise HTTPException(400,'Service is not provisioned')
    await refresh_artifact(db,w);audit(db,u,'workload.reinstall',f'workload:{wid}',request.client.host if request.client else '');db.commit();return RedirectResponse(f'/servers/{wid}',303)

@app.get('/api/workloads/{wid}/status')
async def workload_status(wid:int,u:User=Depends(current),db:Session=Depends(get_db)):
    w=accessible_workload(wid,u,db);deployments=[]
    if w.railway_service_id:
        try:deployments=await RailwayClient().deployments(w.railway_service_id)
        except Exception as e:return {'state':w.state.value,'provider_error':str(e),'deployments':[]}
    return {'state':w.state.value,'service_id':w.railway_service_id,'deployments':deployments}

@app.post('/servers/{wid}/replace')
async def replace_artifact(wid:int,request:Request,file:UploadFile=File(...),entrypoint:str=Form(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db)
    try:filename=safe_filename(file.filename or 'upload');entrypoint=safe_filename(entrypoint)
    except ValueError as e:raise HTTPException(400,str(e))
    data=await file.read(s.max_upload_mb*1024*1024+1)
    if len(data)>s.max_upload_mb*1024*1024:raise HTTPException(413,'Upload too large')
    if filename.endswith('.zip'):inspect_zip(data)
    elif not ((w.runtime=='python' and filename.endswith('.py')) or (w.runtime=='node' and filename.endswith('.js'))):raise HTTPException(400,'File does not match runtime')
    old=w.artifact;db.add(Backup(workload_id=wid,name='Automatic pre-deploy backup',filename=old.filename,sha256=old.sha256,size=old.size,data=old.data))
    a=Artifact(owner_id=w.user_id,filename=filename,content_type=file.content_type or 'application/octet-stream',sha256=hashlib.sha256(data).hexdigest(),size=len(data),data=data);db.add(a);db.flush();w.artifact_id=a.id;w.entrypoint=entrypoint;audit(db,u,'artifact.replace',f'workload:{wid}',request.client.host if request.client else '',{'sha256':a.sha256});db.commit()
    if w.railway_service_id:await refresh_artifact(db,w)
    return RedirectResponse(f'/servers/{wid}?tab=files',303)

@app.post('/servers/{wid}/variables')
async def add_variable(wid:int,request:Request,name:str=Form(...),value:str=Form(...),secret:bool=Form(False),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);name=name.strip().upper();reserved={'CONTROL_PLANE_URL','RUNNER_TOKEN','WORKLOAD_ID','ENTRYPOINT','RUNTIME'}
    if not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,79}',name) or name in reserved:raise HTTPException(400,'Invalid or reserved variable name')
    row=db.scalar(select(WorkloadVariable).where(WorkloadVariable.workload_id==wid,WorkloadVariable.name==name))
    if row:row.encrypted_value=encrypt_secret(value);row.is_secret=secret
    else:db.add(WorkloadVariable(workload_id=wid,name=name,encrypted_value=encrypt_secret(value),is_secret=secret))
    audit(db,u,'variable.upsert',f'workload:{wid}',request.client.host if request.client else '',{'name':name});db.commit()
    if w.railway_service_id:
        await RailwayClient().upsert_variables(w.railway_service_id,{name:value});await RailwayClient().redeploy(w.railway_service_id)
    return RedirectResponse(f'/servers/{wid}?tab=variables',303)

@app.post('/servers/{wid}/variables/{vid}/delete')
async def delete_variable(wid:int,vid:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);row=db.get(WorkloadVariable,vid)
    if not row or row.workload_id!=wid:raise HTTPException(404)
    name=row.name;audit(db,u,'variable.delete',f'workload:{wid}',request.client.host if request.client else '',{'name':name});db.delete(row);db.commit()
    if w.railway_service_id:await RailwayClient().upsert_variables(w.railway_service_id,{name:''});await RailwayClient().redeploy(w.railway_service_id)
    return RedirectResponse(f'/servers/{wid}?tab=variables',303)

@app.post('/servers/{wid}/backups')
def create_backup(wid:int,request:Request,name:str=Form('Manual backup'),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);a=w.artifact;b=Backup(workload_id=wid,name=name[:100] or 'Manual backup',filename=a.filename,sha256=a.sha256,size=a.size,data=a.data);db.add(b);audit(db,u,'backup.create',f'workload:{wid}',request.client.host if request.client else '');db.commit();return RedirectResponse(f'/servers/{wid}?tab=backups',303)
@app.get('/servers/{wid}/backups/{bid}')
def download_backup(wid:int,bid:int,u:User=Depends(current),db:Session=Depends(get_db)):
    accessible_workload(wid,u,db,'files');b=db.get(Backup,bid)
    if not b or b.workload_id!=wid:raise HTTPException(404)
    name=re.sub(r'[^A-Za-z0-9_.-]','_',b.filename);return Response(b.data,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{name}"'})
@app.post('/servers/{wid}/backups/{bid}/delete')
def delete_backup(wid:int,bid:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    owned_workload(wid,u,db);b=db.get(Backup,bid)
    if not b or b.workload_id!=wid:raise HTTPException(404)
    db.delete(b);audit(db,u,'backup.delete',f'workload:{wid}',request.client.host if request.client else '');db.commit();return RedirectResponse(f'/servers/{wid}?tab=backups',303)

@app.post('/servers/{wid}/members')
def add_member(wid:int,request:Request,telegram_id:int=Form(...),permissions:str=Form('view,logs'),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);target=db.scalar(select(User).where(User.telegram_id==telegram_id))
    if not target:raise HTTPException(400,'User must sign in to BlazeNXT first')
    if target.id==w.user_id:raise HTTPException(400,'Owner already has full access')
    allowed={'view','logs','control','files'};perms=[x for x in permissions.split(',') if x in allowed]
    row=db.scalar(select(WorkloadMember).where(WorkloadMember.workload_id==wid,WorkloadMember.user_id==target.id))
    if row:row.permissions=json.dumps(perms)
    else:db.add(WorkloadMember(workload_id=wid,user_id=target.id,permissions=json.dumps(perms)))
    audit(db,u,'member.upsert',f'workload:{wid}',request.client.host if request.client else '',{'user_id':target.id,'permissions':perms});db.commit();return RedirectResponse(f'/servers/{wid}?tab=members',303)
@app.post('/servers/{wid}/members/{mid}/delete')
def delete_member(wid:int,mid:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    owned_workload(wid,u,db);m=db.get(WorkloadMember,mid)
    if not m or m.workload_id!=wid:raise HTTPException(404)
    db.delete(m);audit(db,u,'member.delete',f'workload:{wid}',request.client.host if request.client else '');db.commit();return RedirectResponse(f'/servers/{wid}?tab=members',303)

@app.post('/servers/{wid}/schedules')
def add_schedule(wid:int,request:Request,name:str=Form(...),action_name:str=Form(...),interval_minutes:int=Form(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    owned_workload(wid,u,db)
    if action_name not in {'start','stop','restart'} or not 5<=interval_minutes<=43200:raise HTTPException(400,'Invalid schedule')
    db.add(Schedule(workload_id=wid,name=name[:100],action=action_name,interval_minutes=interval_minutes,next_run=datetime.now(timezone.utc)+timedelta(minutes=interval_minutes)));audit(db,u,'schedule.create',f'workload:{wid}',request.client.host if request.client else '');db.commit();return RedirectResponse(f'/servers/{wid}?tab=schedules',303)
@app.post('/servers/{wid}/schedules/{sid}/delete')
def delete_schedule(wid:int,sid:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    owned_workload(wid,u,db);row=db.get(Schedule,sid)
    if not row or row.workload_id!=wid:raise HTTPException(404)
    db.delete(row);audit(db,u,'schedule.delete',f'workload:{wid}',request.client.host if request.client else '');db.commit();return RedirectResponse(f'/servers/{wid}?tab=schedules',303)

@app.post('/servers/{wid}/deployments/{did}/rollback')
async def rollback_deployment(wid:int,did:str,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=accessible_workload(wid,u,db,'control');deployments=await RailwayClient().deployments(w.railway_service_id)
    if did not in {x['id'] for x in deployments}:raise HTTPException(404,'Deployment not found')
    await RailwayClient().rollback(did);w.state=State.running;audit(db,u,'deployment.rollback',f'workload:{wid}',request.client.host if request.client else '',{'deployment_id':did});db.commit();return RedirectResponse(f'/servers/{wid}?tab=deployments',303)

@app.get('/store',response_class=HTMLResponse)
def store(request:Request,u:User=Depends(current)):
    plans=[{'name':'Free','price':'₹0','slots':2,'features':['Telegram + web sync','Logs and controls','Manual backups']},{'name':'Premium','price':'Manual','slots':20,'features':['Schedules and variables','Collaborators','Priority support']},{'name':'Admin','price':'Private','slots':100,'features':['Platform operations','User management','Extended quotas']}]
    return templates.TemplateResponse('store.html',ctx(request,u,plans=plans))
@app.post('/store/request')
def request_plan(request:Request,plan:str=Form(...),message:str=Form(''),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if plan not in {'Premium','Admin'}:raise HTTPException(400,'Invalid plan')
    db.add(SupportTicket(user_id=u.id,category='billing',subject=f'{plan} plan request',message=message[:2000] or f'Please review my {plan} upgrade request.'));audit(db,u,'plan.request','billing',request.client.host if request.client else '',{'plan':plan});db.commit();return RedirectResponse('/support',303)
@app.get('/support',response_class=HTMLResponse)
def support(request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    tickets=db.scalars(select(SupportTicket).where(SupportTicket.user_id==u.id).order_by(SupportTicket.created_at.desc())).all();return templates.TemplateResponse('support.html',ctx(request,u,tickets=tickets))
@app.post('/support')
def create_ticket(request:Request,category:str=Form(...),subject:str=Form(...),message:str=Form(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if category not in {'technical','billing','abuse','feature'} or not 3<=len(subject)<=120 or not 10<=len(message)<=5000:raise HTTPException(400,'Invalid ticket')
    ticket=SupportTicket(user_id=u.id,category=category,subject=subject,message=message);db.add(ticket);db.flush();audit(db,u,'ticket.create',f'ticket:{ticket.id}',request.client.host if request.client else '');db.commit();return RedirectResponse('/support',303)
@app.post('/admin/tickets/{tid}')
def update_ticket(tid:int,request:Request,status:str=Form(...),admin_note:str=Form(''),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner}:raise HTTPException(403)
    if status not in {'open','in_progress','resolved','closed'}:raise HTTPException(400)
    ticket=db.get(SupportTicket,tid)
    if not ticket:raise HTTPException(404)
    ticket.status=status;ticket.admin_note=admin_note[:5000];db.add(Notification(user_id=ticket.user_id,title=f'Ticket #{ticket.id} updated',message=f'Status: {status}. {admin_note[:300]}'));audit(db,u,'ticket.update',f'ticket:{tid}',request.client.host if request.client else '');db.commit();return RedirectResponse('/admin#tickets',303)

@app.get('/internal/artifacts/{wid}')
def artifact(wid:int,request:Request,db:Session=Depends(get_db)):
    token=request.headers.get('Authorization','').removeprefix('Bearer ');rt=db.scalar(select(RunnerToken).where(RunnerToken.workload_id==wid,RunnerToken.token_hash==hash_token(token))) if token else None;now=datetime.now(timezone.utc)
    if not rt or rt.expires_at<now:raise HTTPException(401,'Invalid runner token')
    w=db.get(Workload,wid);return Response(w.artifact.data,media_type=w.artifact.content_type,headers={'X-Filename':w.artifact.filename,'X-Content-SHA256':w.artifact.sha256,'Cache-Control':'no-store'})
@app.post('/telegram/webhook/{secret}')
async def telegram(secret:str,request:Request):
    if not secrets.compare_digest(secret,s.telegram_webhook_secret):raise HTTPException(404)
    from app.telegram_bot import handle_update
    await handle_update(await request.json());return {'ok':True}
@app.get('/admin',response_class=HTMLResponse)
def admin(request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner}:raise HTTPException(403)
    users=db.scalars(select(User).order_by(User.created_at.desc()).limit(200)).all();audits=db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).all();tickets=db.scalars(select(SupportTicket).order_by(SupportTicket.created_at.desc()).limit(100)).all();workload_count=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0
    return templates.TemplateResponse('admin.html',ctx(request,u,users=users,audits=audits,tickets=tickets,workload_count=workload_count,global_limit=s.global_workload_limit))
@app.post('/admin/users/{uid}')
def update_user(uid:int,request:Request,role:str=Form(...),banned:bool=Form(False),quota_value:str=Form(''),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if u.role!=Role.owner:raise HTTPException(403)
    t=db.get(User,uid)
    if not t:raise HTTPException(404)
    t.role=Role(role);t.banned=banned;t.quota=int(quota_value) if quota_value.strip() else None;audit(db,u,'user.update',f'user:{uid}',request.client.host if request.client else '');db.commit();return RedirectResponse('/admin',303)
@app.get('/health/live')
def live():return {'status':'ok'}
@app.get('/health/ready')
def ready(db:Session=Depends(get_db)):db.execute(select(1));return {'status':'ready','railway_configured':RailwayClient().configured,'telegram_online':BOT_RUNTIME['online']}
@app.get('/health/bot')
def bot_health():return {'online':BOT_RUNTIME['online'],'username':BOT_RUNTIME['username'],'started_at':BOT_RUNTIME['started_at'],'error':BOT_RUNTIME['error']}
@app.get('/metrics')
def metrics():return Response(generate_latest(),media_type=CONTENT_TYPE_LATEST)

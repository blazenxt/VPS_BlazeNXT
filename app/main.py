import asyncio,csv,hashlib,io,json,logging,re,secrets,time,zipfile
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from pathlib import PurePosixPath
from urllib.parse import urlparse
from fastapi import BackgroundTasks,Depends,FastAPI,File,Form,HTTPException,Request,Response,UploadFile
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST,Counter,generate_latest
from sqlalchemy import func,select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api_v1 import router as api_v1_router
from app.auth import login_response,providers as auth_providers,router as auth_router
from app.catalog import PRESETS
from app.config import get_settings
from app.db import Base,SessionLocal,engine,get_db
from app.models import Announcement,ApiKey,ApiRequestLog,Artifact,AuditLog,AuthIdentity,AuthIdentityBlock,Backup,HealthSnapshot,Incident,ManagedDatabase,Notification,PlanEvent,PlatformSetting,ProcessedTelegramUpdate,ReferralCode,ReferralRedemption,Role,RunnerToken,Schedule,StagedChange,State,SupportTicket,TelegramUploadDraft,User,UserSessionPolicy,Wallet,WebhookDelivery,Workload,WorkloadAllocation,WorkloadDomain,WorkloadMember,WorkloadVariable,WorkloadWebhook
from app.railway import RailwayClient
from app.security import encrypt_secret,hash_token,inspect_zip,read_session,safe_filename,verify_telegram
from app.services import audit,perform_action,provision,quota,refresh_artifact
from app.webhooks import dispatch_event,validate_webhook_url
s=get_settings();templates=Jinja2Templates(directory='templates');REQ=Counter('blaze_http_requests_total','HTTP requests',['method','path','status']);rate={};logger=logging.getLogger('blazenxt');APP_STARTED=datetime.now(timezone.utc);TELEGRAM_HEADER_SECRET=hashlib.sha256((s.app_secret+s.telegram_webhook_secret).encode()).hexdigest();BOT_RUNTIME={'online':False,'username':None,'id':None,'webhook':None,'error':None,'started_at':None,'last_checked_at':None,'pending_updates':0,'last_telegram_error':None,'auto_repaired':False}
async def configure_telegram_webhook():
    import httpx
    webhook=f"{s.web_base_url.rstrip('/')}/telegram/webhook/{s.telegram_webhook_secret}"
    commands_list=[{'command':'start','description':'Open BlazeNXT control center'},{'command':'servers','description':'List and control workloads'},{'command':'status','description':'Platform and account status'},{'command':'account','description':'Open account and security'},{'command':'deploy','description':'Upload and deploy code'},{'command':'help','description':'Show deployment help'}]
    async with httpx.AsyncClient(timeout=15) as client:
        identity=await client.post(f'https://api.telegram.org/bot{s.bot_token}/getMe');identity.raise_for_status();bot=identity.json()
        if not bot.get('ok'):raise RuntimeError(bot.get('description','Telegram identity rejected'))
        response=await client.post(f'https://api.telegram.org/bot{s.bot_token}/setWebhook',json={'url':webhook,'secret_token':TELEGRAM_HEADER_SECRET,'drop_pending_updates':False,'allowed_updates':['message','callback_query']});response.raise_for_status();result=response.json()
        if not result.get('ok'):raise RuntimeError(result.get('description','Telegram rejected webhook'))
        commands=await client.post(f'https://api.telegram.org/bot{s.bot_token}/setMyCommands',json={'commands':commands_list});commands.raise_for_status()
    info=bot['result'];BOT_RUNTIME.update({'online':True,'username':info.get('username'),'id':info.get('id'),'webhook':urlparse(webhook).netloc,'error':None,'started_at':BOT_RUNTIME['started_at'] or datetime.now(timezone.utc).isoformat(),'last_checked_at':datetime.now(timezone.utc).isoformat()});logger.info('Telegram bot @%s started with verified webhook sync',info.get('username'))
async def inspect_telegram_runtime(repair=True):
    import httpx
    expected=f"{s.web_base_url.rstrip('/')}/telegram/webhook/{s.telegram_webhook_secret}"
    async with httpx.AsyncClient(timeout=15) as client:
        me=await client.get(f'https://api.telegram.org/bot{s.bot_token}/getMe');me.raise_for_status();me_data=me.json()
        hook=await client.get(f'https://api.telegram.org/bot{s.bot_token}/getWebhookInfo');hook.raise_for_status();hook_data=hook.json()
    if not me_data.get('ok') or not hook_data.get('ok'):raise RuntimeError('Telegram runtime inspection failed')
    info=hook_data['result'];mismatch=info.get('url')!=expected
    if mismatch and repair:
        await configure_telegram_webhook();BOT_RUNTIME['auto_repaired']=True;return await inspect_telegram_runtime(False)
    identity=me_data['result'];BOT_RUNTIME.update({'online':not mismatch,'username':identity.get('username'),'id':identity.get('id'),'webhook':urlparse(info.get('url') or '').netloc,'pending_updates':info.get('pending_update_count',0),'last_telegram_error':info.get('last_error_message'),'last_checked_at':datetime.now(timezone.utc).isoformat(),'error':None if not mismatch else 'Webhook URL mismatch'});return BOT_RUNTIME
async def bot_monitor_worker():
    while True:
        await asyncio.sleep(180)
        try:await inspect_telegram_runtime(True)
        except Exception as e:BOT_RUNTIME.update({'online':False,'error':str(e)[:300],'last_checked_at':datetime.now(timezone.utc).isoformat()});logger.exception('Telegram runtime monitor failed')
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
                latest=db.scalar(select(HealthSnapshot).order_by(HealthSnapshot.created_at.desc()).limit(1));latest_time=latest.created_at if latest else None
                if latest_time and latest_time.tzinfo is None:latest_time=latest_time.replace(tzinfo=timezone.utc)
                if not latest_time or now-latest_time>=timedelta(minutes=5):
                    for workload in db.scalars(select(Workload).where(Workload.state!=State.deleted)).all():db.add(HealthSnapshot(workload_id=workload.id,state=workload.state.value))
                    db.commit()
                cutoff=now-timedelta(days=7)
                for update in db.scalars(select(ProcessedTelegramUpdate).where(ProcessedTelegramUpdate.created_at<cutoff).limit(500)).all():db.delete(update)
                for draft in db.scalars(select(TelegramUploadDraft).where(TelegramUploadDraft.status=='pending',TelegramUploadDraft.expires_at<now).limit(100)).all():
                    artifact=draft.artifact;db.delete(draft);db.flush();db.delete(artifact)
                for draft in db.scalars(select(TelegramUploadDraft).where(TelegramUploadDraft.status=='deployed',TelegramUploadDraft.created_at<cutoff).limit(500)).all():db.delete(draft)
                db.commit()
        except Exception:logger.exception('Schedule worker failed')
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    if s.production and ('change-me' in s.app_secret or not s.bot_token):raise RuntimeError('Production secrets are not configured')
    if s.bot_token and s.web_base_url.startswith('https://') and s.telegram_webhook_secret!='change-me':
        try:await configure_telegram_webhook()
        except Exception as e:BOT_RUNTIME.update({'online':False,'error':str(e)[:300]});logger.exception('Automatic Telegram bot startup failed')
    else:BOT_RUNTIME.update({'online':False,'error':'Telegram variables or HTTPS base URL are incomplete'})
    scheduler=asyncio.create_task(schedule_worker());bot_monitor=asyncio.create_task(bot_monitor_worker()) if s.bot_token else None
    yield
    scheduler.cancel()
    if bot_monitor:bot_monitor.cancel()
app=FastAPI(title='BlazeNXT Control Plane',version='1.0.0',docs_url=None if s.production else '/docs',lifespan=lifespan);app.state.bot_runtime=BOT_RUNTIME;app.include_router(auth_router);app.include_router(api_v1_router)
app.mount('/static',StaticFiles(directory='static'),name='static')
def safe_frame_origins(raw,allow_none=False):
    values=[]
    for item in raw.split(','):
        item=item.strip()
        if item in {"'self'",'self'}:values.append("'self'");continue
        if allow_none and item in {"'none'",'none'}:values.append("'none'");continue
        parsed=urlparse(item)
        if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and parsed.path in ('','/') and not parsed.query and not parsed.fragment:values.append(f'https://{parsed.netloc}')
    if allow_none and "'none'" in values:return ["'none'"]
    return list(dict.fromkeys(values))
FRAME_ANCESTORS=safe_frame_origins(s.frame_ancestors,True) or ["'none'"]
FRAME_SOURCES=safe_frame_origins(s.frame_sources) or ['https://oauth.telegram.org']
@app.middleware('http')
async def security_headers(request,call_next):
    ip=request.client.host if request.client else 'unknown';key=f'{ip}:{request.url.path}';now=time.time();hits=[x for x in rate.get(key,[]) if now-x<60];limit=20 if request.url.path.startswith(('/auth','/api')) else 120
    if len(hits)>=limit:return JSONResponse({'detail':'rate limit exceeded'},429)
    hits.append(now);rate[key]=hits;response=await call_next(request)
    csp="default-src 'self'; img-src 'self' data: https://t.me; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; frame-src 'self' "+' '.join(FRAME_SOURCES)+"; frame-ancestors "+' '.join(FRAME_ANCESTORS)
    headers={'X-Content-Type-Options':'nosniff','Referrer-Policy':'strict-origin-when-cross-origin','Permissions-Policy':'camera=(), microphone=(), geolocation=()','Content-Security-Policy':csp,'Strict-Transport-Security':'max-age=31536000; includeSubDomains'}
    if FRAME_ANCESTORS==["'none'"]:headers['X-Frame-Options']='DENY'
    elif FRAME_ANCESTORS==["'self'"]:headers['X-Frame-Options']='SAMEORIGIN'
    response.headers.update(headers)
    REQ.labels(request.method,request.url.path,response.status_code).inc();return response
def current(request:Request,db:Session=Depends(get_db)):
    p=read_session(request.cookies.get('blaze_session',''))
    if not p:raise HTTPException(401,'Sign in required')
    u=db.get(User,int(p['uid']))
    if not u or u.banned:raise HTTPException(403,'Account unavailable')
    policy=db.scalar(select(UserSessionPolicy).where(UserSessionPolicy.user_id==u.id))
    if policy:
        issued=float(p.get('iat',0));revoked=policy.revoked_before
        if revoked.tzinfo is None:revoked=revoked.replace(tzinfo=timezone.utc)
        if issued<=revoked.timestamp():raise HTTPException(401,'Session was revoked; sign in again')
    request.state.session=p;return u
def csrf(request:Request,token:str=Form(...)):
    p=getattr(request.state,'session',None) or read_session(request.cookies.get('blaze_session',''))
    if not p or not secrets.compare_digest(p.get('csrf',''),token):raise HTTPException(403,'Invalid CSRF token')
def ctx(request,user=None,**extra):
    p=read_session(request.cookies.get('blaze_session','')) or {};return {'request':request,'user':user,'csrf':p.get('csrf',''),'bot_username':s.bot_username,'bot_runtime':BOT_RUNTIME,**extra}
def platform_setting(db,key,default='true'):
    row=db.get(PlatformSetting,key);return row.value if row else default
def deployments_enabled(db):return platform_setting(db,'deployments_enabled','true')=='true'
@app.get('/status',response_class=HTMLResponse)
def public_status(request:Request,db:Session=Depends(get_db)):
    incidents=db.scalars(select(Incident).order_by(Incident.created_at.desc()).limit(25)).all();announcements=db.scalars(select(Announcement).where(Announcement.active==True).order_by(Announcement.created_at.desc()).limit(5)).all();total=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0;online=db.scalar(select(func.count()).select_from(Workload).where(Workload.state==State.running)) or 0;failed=db.scalar(select(func.count()).select_from(Workload).where(Workload.state==State.failed)) or 0;since=datetime.now(timezone.utc)-timedelta(hours=24);snapshots=db.scalars(select(HealthSnapshot).where(HealthSnapshot.created_at>=since)).all();uptime=round(100*sum(x.state!='failed' for x in snapshots)/len(snapshots),2) if snapshots else (100 if failed==0 else 0);operational=BOT_RUNTIME['online'] and not any(x.status!='resolved' and x.impact in {'major','critical'} for x in incidents)
    return templates.TemplateResponse('status.html',ctx(request,status_data={'operational':operational,'total':total,'online':online,'failed':failed,'uptime':uptime,'started_at':APP_STARTED},incidents=incidents,announcements=announcements))
@app.get('/api/status')
def public_status_api(db:Session=Depends(get_db)):
    total=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0;online=db.scalar(select(func.count()).select_from(Workload).where(Workload.state==State.running)) or 0;active=db.scalar(select(func.count()).select_from(Incident).where(Incident.status!='resolved')) or 0;return {'status':'operational' if active==0 else 'degraded','services':{'total':total,'online':online},'active_incidents':active,'telegram_online':BOT_RUNTIME['online']}
@app.get('/',response_class=HTMLResponse)
def home(request:Request,db:Session=Depends(get_db)):
    announcements=db.scalars(select(Announcement).where(Announcement.active==True).order_by(Announcement.created_at.desc()).limit(3)).all();return templates.TemplateResponse('home.html',ctx(request,auth_providers=auth_providers(),announcements=announcements))
@app.get('/auth/telegram')
def auth(request:Request,db:Session=Depends(get_db)):
    data=dict(request.query_params)
    if not s.bot_token or not verify_telegram(data):raise HTTPException(401,'Invalid or expired Telegram login')
    tid=int(data['id']);existing=db.scalar(select(User).where(User.telegram_id==tid));session=read_session(request.cookies.get('blaze_session',''));linked=db.get(User,int(session['uid'])) if session else None;name=' '.join(filter(None,[data.get('first_name'),data.get('last_name')])) or 'User'
    if linked and existing and linked.id!=existing.id:raise HTTPException(409,'This Telegram account is already linked to another BlazeNXT user')
    if linked and linked.telegram_id>=0 and linked.telegram_id!=tid:raise HTTPException(409,'A different Telegram account is already linked')
    if linked and not existing:
        u=linked
        if u.telegram_id<0:u.telegram_id=tid
    else:u=existing
    if not u:u=User(telegram_id=tid,username=data.get('username'),display_name=name,role=Role.owner if tid in s.owners else Role.user);db.add(u)
    else:u.username=data.get('username');u.display_name=name;u.role=Role.owner if tid in s.owners else u.role
    db.flush();identity=db.scalar(select(AuthIdentity).where(AuthIdentity.provider=='telegram',AuthIdentity.subject==str(tid)))
    if linked:
        block=db.scalar(select(AuthIdentityBlock).where(AuthIdentityBlock.provider=='telegram',AuthIdentityBlock.subject==str(tid),AuthIdentityBlock.user_id==u.id))
        if block:db.delete(block)
    if not identity:db.add(AuthIdentity(user_id=u.id,provider='telegram',subject=str(tid),display_name=name,avatar_url=data.get('photo_url')))
    db.commit();db.refresh(u);audit(db,u,'login','web',request.client.host if request.client else '');db.commit();return login_response(u.id,db,'telegram')
@app.post('/logout')
def logout(request:Request,u=Depends(current),_=Depends(csrf)):
    r=RedirectResponse('/',303);r.delete_cookie('blaze_session');return r
@app.get('/dashboard',response_class=HTMLResponse)
def dashboard(request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    shared_ids=db.scalars(select(WorkloadMember.workload_id).where(WorkloadMember.user_id==u.id)).all()
    ws=db.scalars(select(Workload).where((Workload.user_id==u.id)|(Workload.id.in_(shared_ids)),Workload.state!=State.deleted).order_by(Workload.created_at.desc())).all()
    notes=db.scalars(select(Notification).where(Notification.user_id==u.id).order_by(Notification.created_at.desc()).limit(5)).all()
    global_active=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0;visible_ids=[w.id for w in ws];allocations=db.scalars(select(WorkloadAllocation).where(WorkloadAllocation.workload_id.in_(visible_ids))).all() if visible_ids else [];container_count=sum(x.replicas for x in allocations)+(len(ws)-len(allocations))
    return templates.TemplateResponse('dashboard.html',ctx(request,u,workloads=ws,container_count=container_count,quota=quota(u),notifications=notes,global_active=global_active,global_limit=s.global_workload_limit,presets=PRESETS))
def configured_embed_tools():
    try:items=json.loads(s.embed_tools_json)
    except json.JSONDecodeError:return []
    tools=[]
    for item in items if isinstance(items,list) else []:
        if not isinstance(item,dict):continue
        parsed=urlparse(str(item.get('url','')));origin=f'{parsed.scheme}://{parsed.netloc}'
        if parsed.scheme=='https' and parsed.hostname and origin in FRAME_SOURCES:tools.append({'name':str(item.get('name','Embedded tool'))[:80],'url':str(item['url'])})
    return tools
@app.get('/tools',response_class=HTMLResponse)
def embedded_tools(request:Request,u:User=Depends(current)):
    return templates.TemplateResponse('tools.html',ctx(request,u,tools=configured_embed_tools()))
def visible_workloads(db,u,scope='mine'):
    if scope=='all' and u.role in {Role.admin,Role.owner}:return db.scalars(select(Workload).where(Workload.state!=State.deleted).order_by(Workload.created_at.desc())).all()
    shared=db.scalars(select(WorkloadMember.workload_id).where(WorkloadMember.user_id==u.id)).all();return db.scalars(select(Workload).where((Workload.user_id==u.id)|(Workload.id.in_(shared)),Workload.state!=State.deleted).order_by(Workload.created_at.desc())).all()
@app.get('/project',response_class=HTMLResponse)
def project_canvas(request:Request,scope:str='mine',u:User=Depends(current),db:Session=Depends(get_db)):
    workloads=visible_workloads(db,u,scope);ids=[w.id for w in workloads];allocations={x.workload_id:x for x in db.scalars(select(WorkloadAllocation).where(WorkloadAllocation.workload_id.in_(ids))).all()} if ids else {};databases=db.scalars(select(ManagedDatabase).where(ManagedDatabase.workload_id.in_(ids))).all() if ids else [];domains=db.scalars(select(WorkloadDomain).where(WorkloadDomain.workload_id.in_(ids))).all() if ids else [];pending=db.scalar(select(func.count()).select_from(StagedChange).where(StagedChange.user_id==u.id,StagedChange.status=='pending')) or 0
    return templates.TemplateResponse('project.html',ctx(request,u,workloads=workloads,allocations=allocations,databases=databases,domains=domains,scope=scope,pending=pending))
@app.get('/observability',response_class=HTMLResponse)
def observability(request:Request,scope:str='mine',u:User=Depends(current),db:Session=Depends(get_db)):
    workloads=visible_workloads(db,u,scope);ids=[w.id for w in workloads];allocations=db.scalars(select(WorkloadAllocation).where(WorkloadAllocation.workload_id.in_(ids))).all() if ids else [];events=db.scalars(select(AuditLog).where(AuditLog.target.in_([f'workload:{x}' for x in ids])).order_by(AuditLog.created_at.desc()).limit(100)).all() if ids else [];hooks=db.scalars(select(WorkloadWebhook.id).where(WorkloadWebhook.workload_id.in_(ids))).all() if ids else [];deliveries=db.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id.in_(hooks)).order_by(WebhookDelivery.created_at.desc()).limit(50)).all() if hooks else []
    stats={'total':len(workloads),'online':sum(w.state==State.running for w in workloads),'failed':sum(w.state==State.failed for w in workloads),'containers':sum(a.replicas for a in allocations)+(len(workloads)-len(allocations)),'memory_mb':sum(a.memory_mb*a.replicas for a in allocations),'cpu':sum(float(a.cpu_vcpus)*a.replicas for a in allocations)}
    return templates.TemplateResponse('observability.html',ctx(request,u,workloads=workloads,events=events,deliveries=deliveries,stats=stats,scope=scope))
@app.get('/api/observability')
def observability_api(scope:str='mine',u:User=Depends(current),db:Session=Depends(get_db)):
    rows=visible_workloads(db,u,scope);return {'services':[{'id':w.id,'name':w.name,'state':w.state.value,'updated_at':w.updated_at} for w in rows],'summary':{'total':len(rows),'online':sum(w.state==State.running for w in rows),'failed':sum(w.state==State.failed for w in rows)}}
@app.get('/changes',response_class=HTMLResponse)
def changes(request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    rows=db.scalars(select(StagedChange).where(StagedChange.user_id==u.id,StagedChange.status=='pending').order_by(StagedChange.created_at.desc())).all();workloads=visible_workloads(db,u);return templates.TemplateResponse('changes.html',ctx(request,u,changes=rows,workloads=workloads,presets=PRESETS))
@app.post('/changes')
def stage_change(request:Request,workload_id:int=Form(...),kind:str=Form(...),runtime:str=Form(''),entrypoint:str=Form(''),cpu_vcpus:str=Form(''),memory_mb:str=Form(''),replicas:str=Form(''),restart_policy:str=Form('ON_FAILURE'),variable_name:str=Form(''),variable_value:str=Form(''),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=accessible_workload(workload_id,u,db,'control');payload={}
    if kind=='startup':
        if runtime not in {'python','node'}:raise HTTPException(400,'Invalid runtime')
        try:clean_entrypoint=safe_filename(entrypoint)
        except ValueError as e:raise HTTPException(400,str(e))
        payload={'runtime':runtime,'entrypoint':clean_entrypoint}
    elif kind=='resources':
        try:cpu=float(cpu_vcpus);memory=int(memory_mb);replica_count=int(replicas)
        except ValueError:raise HTTPException(400,'Invalid resource values')
        if not 0.1<=cpu<=32 or not 128<=memory<=131072 or not 1<=replica_count<=20 or restart_policy not in {'ON_FAILURE','ALWAYS','NEVER'}:raise HTTPException(400,'Resource values outside allowed range')
        payload={'cpu_vcpus':cpu,'memory_mb':memory,'replicas':replica_count,'restart_policy':restart_policy,'restart_retries':5}
    elif kind=='variable':
        name=variable_name.strip().upper()
        if not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,79}',name) or name in {'CONTROL_PLANE_URL','RUNNER_TOKEN','WORKLOAD_ID','ENTRYPOINT','RUNTIME'}:raise HTTPException(400,'Invalid or reserved variable name')
        payload={'name':name,'value':variable_value}
    else:raise HTTPException(400,'Unsupported change type')
    db.add(StagedChange(user_id=u.id,workload_id=w.id,kind=kind,payload=json.dumps(payload)));audit(db,u,'change.stage',f'workload:{w.id}',request.client.host if request.client else '',{'kind':kind});db.commit();return RedirectResponse('/changes',303)
@app.post('/changes/apply')
async def apply_changes(request:Request,commit_message:str=Form('Apply staged changes'),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    rows=db.scalars(select(StagedChange).where(StagedChange.user_id==u.id,StagedChange.status=='pending').order_by(StagedChange.created_at)).all()
    for change in rows:
        w=accessible_workload(change.workload_id,u,db,'control');data=json.loads(change.payload);client=RailwayClient()
        if change.kind=='startup':w.runtime=data['runtime'];w.entrypoint=data['entrypoint'];await refresh_artifact(db,w)
        elif change.kind=='resources':
            await client.update_limits(w.railway_service_id,data['cpu_vcpus'],data['memory_mb']);await client.update_instance(w.railway_service_id,data['replicas'],data['restart_policy'],data['restart_retries']);allocation=db.scalar(select(WorkloadAllocation).where(WorkloadAllocation.workload_id==w.id)) or WorkloadAllocation(workload_id=w.id);db.add(allocation);allocation.cpu_vcpus=str(data['cpu_vcpus']);allocation.memory_mb=data['memory_mb'];allocation.replicas=data['replicas'];allocation.restart_policy=data['restart_policy']
        elif change.kind=='variable':
            variable=db.scalar(select(WorkloadVariable).where(WorkloadVariable.workload_id==w.id,WorkloadVariable.name==data['name'])) or WorkloadVariable(workload_id=w.id,name=data['name']);db.add(variable);variable.encrypted_value=encrypt_secret(data['value']);variable.is_secret=True;await client.upsert_variables(w.railway_service_id,{data['name']:data['value']});await client.redeploy(w.railway_service_id)
        change.status='applied';change.commit_message=commit_message[:200];change.applied_at=datetime.now(timezone.utc);audit(db,u,'change.apply',f'workload:{w.id}',request.client.host if request.client else '',{'kind':change.kind});db.commit()
    return RedirectResponse('/project',303)
@app.post('/changes/{change_id}/discard')
def discard_change(change_id:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    row=db.get(StagedChange,change_id)
    if not row or row.user_id!=u.id or row.status!='pending':raise HTTPException(404)
    row.status='discarded';audit(db,u,'change.discard',f'workload:{row.workload_id}',request.client.host if request.client else '');db.commit();return RedirectResponse('/changes',303)
@app.post('/workloads')
async def upload(request:Request,bg:BackgroundTasks,name:str=Form(...),runtime:str=Form(...),entrypoint:str=Form(...),preset:str=Form('custom'),file:UploadFile=File(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    active=db.scalar(select(func.count()).select_from(Workload).where(Workload.user_id==u.id,Workload.state!=State.deleted)) or 0
    if not deployments_enabled(db):raise HTTPException(503,'New deployments are temporarily disabled by an administrator')
    global_active=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0
    if s.global_workload_limit and global_active>=s.global_workload_limit:raise HTTPException(503,'Platform capacity reached. An administrator must raise GLOBAL_WORKLOAD_LIMIT or upgrade Railway.')
    if active>=quota(u):raise HTTPException(403,'Workload quota reached')
    if preset in PRESETS:runtime=PRESETS[preset]['runtime'];entrypoint=PRESETS[preset]['entrypoint']
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
        await dispatch_event(wid,f'workload.{action}',{'source':'web','state':w.state.value})
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
    allocation=db.scalar(select(WorkloadAllocation).where(WorkloadAllocation.workload_id==wid));databases=db.scalars(select(ManagedDatabase).where(ManagedDatabase.workload_id==wid).order_by(ManagedDatabase.created_at.desc())).all();domains=db.scalars(select(WorkloadDomain).where(WorkloadDomain.workload_id==wid).order_by(WorkloadDomain.created_at.desc())).all();webhooks=db.scalars(select(WorkloadWebhook).where(WorkloadWebhook.workload_id==wid).order_by(WorkloadWebhook.created_at.desc())).all();hook_ids=[x.id for x in webhooks];deliveries=db.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id.in_(hook_ids)).order_by(WebhookDelivery.created_at.desc()).limit(50)).all() if hook_ids else []
    is_owner=w.user_id==u.id or u.role in {Role.admin,Role.owner}
    return templates.TemplateResponse('server.html',ctx(request,u,w=w,tab=tab,deployments=deployments,provider_error=provider_error,files=artifact_files(w.artifact),events=events,variables=variables,backups=backups,members=members,schedules=schedules,is_owner=is_owner,presets=PRESETS,webhooks=webhooks,deliveries=deliveries,database_enabled=s.enable_database_provisioning,allocation=allocation,databases=databases,domains=domains,active_database_count=sum(1 for d in databases if d.state=='active'),max_databases=s.max_databases_per_workload))

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

@app.post('/servers/{wid}/startup')
async def update_startup(wid:int,request:Request,runtime:str=Form(...),entrypoint:str=Form(...),preset:str=Form('custom'),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db)
    if preset in PRESETS:runtime=PRESETS[preset]['runtime'];entrypoint=PRESETS[preset]['entrypoint']
    if runtime not in {'python','node'}:raise HTTPException(400,'Invalid runtime')
    try:entrypoint=safe_filename(entrypoint)
    except ValueError as e:raise HTTPException(400,str(e))
    w.runtime=runtime;w.entrypoint=entrypoint;audit(db,u,'startup.update',f'workload:{wid}',request.client.host if request.client else '',{'runtime':runtime,'entrypoint':entrypoint,'preset':preset});db.commit()
    if w.railway_service_id:await refresh_artifact(db,w)
    await dispatch_event(wid,'startup.updated',{'runtime':runtime,'entrypoint':entrypoint});return RedirectResponse(f'/servers/{wid}?tab=startup',303)

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

@app.post('/servers/{wid}/resources')
async def update_resources(wid:int,request:Request,cpu_vcpus:float=Form(...),memory_mb:int=Form(...),replicas:int=Form(...),restart_policy:str=Form(...),restart_retries:int=Form(5),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db)
    if not 0.1<=cpu_vcpus<=32 or not 128<=memory_mb<=131072 or not 1<=replicas<=20 or restart_policy not in {'ON_FAILURE','ALWAYS','NEVER'} or not 0<=restart_retries<=100:raise HTTPException(400,'Invalid resource allocation')
    if not w.railway_service_id:raise HTTPException(400,'Service is not provisioned')
    client=RailwayClient();await client.update_limits(w.railway_service_id,cpu_vcpus,memory_mb);await client.update_instance(w.railway_service_id,replicas,restart_policy,restart_retries);row=db.scalar(select(WorkloadAllocation).where(WorkloadAllocation.workload_id==wid))
    if not row:row=WorkloadAllocation(workload_id=wid);db.add(row)
    row.cpu_vcpus=str(cpu_vcpus);row.memory_mb=memory_mb;row.replicas=replicas;row.restart_policy=restart_policy;row.restart_retries=restart_retries;audit(db,u,'resources.update',f'workload:{wid}',request.client.host if request.client else '',{'cpu':cpu_vcpus,'memory_mb':memory_mb,'replicas':replicas});db.commit();await dispatch_event(wid,'resources.updated',{'cpu':cpu_vcpus,'memory_mb':memory_mb,'replicas':replicas});return RedirectResponse(f'/servers/{wid}?tab=resources',303)
@app.post('/servers/{wid}/suspension/{mode}')
async def suspension(wid:int,mode:str,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db)
    if mode not in {'suspend','unsuspend'}:raise HTTPException(400)
    row=db.scalar(select(WorkloadAllocation).where(WorkloadAllocation.workload_id==wid))
    if not row:row=WorkloadAllocation(workload_id=wid,cpu_vcpus=str(s.default_cpu_vcpus),memory_mb=s.default_memory_mb);db.add(row)
    if mode=='suspend':
        deployments=await RailwayClient().deployments(w.railway_service_id)
        if deployments:await RailwayClient().stop(deployments[0]['id'])
        row.suspended=True;w.state=State.stopped
    else:row.suspended=False;await RailwayClient().redeploy(w.railway_service_id);w.state=State.running
    audit(db,u,f'workload.{mode}',f'workload:{wid}',request.client.host if request.client else '');db.commit();await dispatch_event(wid,f'workload.{mode}',{});return RedirectResponse(f'/servers/{wid}?tab=settings',303)
@app.post('/servers/{wid}/databases')
async def create_database(wid:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db)
    if not s.enable_database_provisioning:raise HTTPException(403,'Database provisioning is disabled')
    all_records=db.scalars(select(ManagedDatabase).where(ManagedDatabase.workload_id==wid)).all();existing=[x for x in all_records if x.state=='active']
    if len(existing)>=s.max_databases_per_workload:raise HTTPException(403,'Database limit reached')
    index=len(all_records)+1;service_name=f'blaze-db-{w.user_id}-{wid}-{index}';database_name=f'blaze_{wid}_{index}';username=f'blaze_{wid}';password=secrets.token_urlsafe(32);client=RailwayClient();sid=None
    try:
        sid=await client.create_image_service(service_name,'postgres:17-alpine',{'POSTGRES_DB':database_name,'POSTGRES_USER':username,'POSTGRES_PASSWORD':password,'PGDATA':'/var/lib/postgresql/data/pgdata'});volume_id=await client.create_volume(sid,'/var/lib/postgresql/data');host=f'{service_name}.railway.internal';url=f'postgresql://{username}:{password}@{host}:5432/{database_name}';row=ManagedDatabase(workload_id=wid,railway_service_id=sid,railway_volume_id=volume_id,service_name=service_name,database_name=database_name,username=username,encrypted_password=encrypt_secret(password),state='active');db.add(row);variable=db.scalar(select(WorkloadVariable).where(WorkloadVariable.workload_id==wid,WorkloadVariable.name=='DATABASE_URL'))
        if variable:variable.encrypted_value=encrypt_secret(url);variable.is_secret=True
        else:db.add(WorkloadVariable(workload_id=wid,name='DATABASE_URL',encrypted_value=encrypt_secret(url),is_secret=True))
        db.commit();await client.upsert_variables(w.railway_service_id,{'DATABASE_URL':url});await client.redeploy(w.railway_service_id);audit(db,u,'database.create',f'workload:{wid}',request.client.host if request.client else '',{'service_id':sid});db.commit();await dispatch_event(wid,'database.created',{'engine':'postgresql'});return RedirectResponse(f'/servers/{wid}?tab=databases',303)
    except Exception:
        if sid:
            try:await client.delete(sid)
            except Exception:pass
        raise
@app.post('/servers/{wid}/databases/{database_id}/delete')
async def delete_database(wid:int,database_id:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db);row=db.get(ManagedDatabase,database_id)
    if not row or row.workload_id!=wid:raise HTTPException(404)
    await RailwayClient().delete(row.railway_service_id);variable=db.scalar(select(WorkloadVariable).where(WorkloadVariable.workload_id==wid,WorkloadVariable.name=='DATABASE_URL'))
    if variable:db.delete(variable)
    row.state='archived';audit(db,u,'database.service_delete',f'workload:{wid}',request.client.host if request.client else '',{'volume_retained':row.railway_volume_id});db.commit();await RailwayClient().upsert_variables(w.railway_service_id,{'DATABASE_URL':''});await RailwayClient().redeploy(w.railway_service_id);await dispatch_event(wid,'database.deleted',{});return RedirectResponse(f'/servers/{wid}?tab=databases',303)

@app.post('/servers/{wid}/domains')
async def create_domain(wid:int,request:Request,kind:str=Form(...),domain:str=Form(''),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    w=owned_workload(wid,u,db)
    if not w.railway_service_id:raise HTTPException(400,'Service is not provisioned')
    client=RailwayClient()
    if kind=='railway':name=await client.create_service_domain(w.railway_service_id);row=WorkloadDomain(workload_id=wid,domain=name,kind='railway',status='active')
    elif kind=='custom':
        domain=domain.strip().lower()
        if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain):raise HTTPException(400,'Invalid domain')
        result=await client.create_custom_domain(w.railway_service_id,domain);records=(result.get('status') or {}).get('dnsRecords') or [];row=WorkloadDomain(workload_id=wid,domain=domain,railway_domain_id=result.get('id'),kind='custom',dns_records=json.dumps(records),status='pending_dns')
    else:raise HTTPException(400,'Invalid domain type')
    db.add(row);audit(db,u,'domain.create',f'workload:{wid}',request.client.host if request.client else '',{'domain':row.domain,'kind':kind});db.commit();return RedirectResponse(f'/servers/{wid}?tab=network',303)

@app.post('/servers/{wid}/webhooks')
def add_webhook(wid:int,request:Request,url:str=Form(...),secret_value:str=Form(...),events:str=Form('*'),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    owned_workload(wid,u,db)
    try:url=validate_webhook_url(url.strip())
    except ValueError as e:raise HTTPException(400,str(e))
    if len(secret_value)<16:raise HTTPException(400,'Webhook secret must be at least 16 characters')
    allowed={'*','workload.start','workload.stop','workload.restart','deployment.completed','deployment.failed','startup.updated'};chosen=[x.strip() for x in events.split(',') if x.strip() in allowed]
    if not chosen:raise HTTPException(400,'No valid events selected')
    db.add(WorkloadWebhook(workload_id=wid,url=url,encrypted_secret=encrypt_secret(secret_value),events=json.dumps(chosen)));audit(db,u,'webhook.create',f'workload:{wid}',request.client.host if request.client else '',{'url':url,'events':chosen});db.commit();return RedirectResponse(f'/servers/{wid}?tab=webhooks',303)
@app.post('/servers/{wid}/webhooks/{hook_id}/delete')
def delete_webhook(wid:int,hook_id:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    owned_workload(wid,u,db);hook=db.get(WorkloadWebhook,hook_id)
    if not hook or hook.workload_id!=wid:raise HTTPException(404)
    db.delete(hook);audit(db,u,'webhook.delete',f'workload:{wid}',request.client.host if request.client else '');db.commit();return RedirectResponse(f'/servers/{wid}?tab=webhooks',303)

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
    ticket.status=status;ticket.admin_note=admin_note[:5000];db.add(Notification(user_id=ticket.user_id,title=f'Ticket #{ticket.id} updated',message=f'Status: {status}. {admin_note[:300]}'));audit(db,u,'ticket.update',f'ticket:{tid}',request.client.host if request.client else '');db.commit();return RedirectResponse('/admin/tickets',303)

@app.get('/internal/artifacts/{wid}')
def artifact(wid:int,request:Request,db:Session=Depends(get_db)):
    token=request.headers.get('Authorization','').removeprefix('Bearer ');rt=db.scalar(select(RunnerToken).where(RunnerToken.workload_id==wid,RunnerToken.token_hash==hash_token(token))) if token else None;now=datetime.now(timezone.utc)
    if not rt or rt.expires_at<now:raise HTTPException(401,'Invalid runner token')
    w=db.get(Workload,wid);return Response(w.artifact.data,media_type=w.artifact.content_type,headers={'X-Filename':w.artifact.filename,'X-Content-SHA256':w.artifact.sha256,'Cache-Control':'no-store'})
@app.post('/telegram/webhook/{secret}')
async def telegram(secret:str,request:Request):
    header=request.headers.get('X-Telegram-Bot-Api-Secret-Token','')
    if not secrets.compare_digest(secret,s.telegram_webhook_secret) or not secrets.compare_digest(header,TELEGRAM_HEADER_SECRET):raise HTTPException(404)
    update=await request.json();update_id=update.get('update_id')
    if not isinstance(update_id,int):raise HTTPException(400,'Invalid Telegram update')
    with SessionLocal() as db:
        if db.scalar(select(ProcessedTelegramUpdate).where(ProcessedTelegramUpdate.update_id==update_id)):return {'ok':True,'duplicate':True}
        db.add(ProcessedTelegramUpdate(update_id=update_id))
        try:db.commit()
        except IntegrityError:db.rollback();return {'ok':True,'duplicate':True}
    from app.telegram_bot import handle_update
    asyncio.create_task(handle_update(update));return {'ok':True}
@app.get('/notifications',response_class=HTMLResponse)
def notifications_page(request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    rows=db.scalars(select(Notification).where(Notification.user_id==u.id).order_by(Notification.created_at.desc()).limit(200)).all();return templates.TemplateResponse('notifications.html',ctx(request,u,notifications=rows))
@app.post('/notifications/read-all')
def read_all_notifications(request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    for row in db.scalars(select(Notification).where(Notification.user_id==u.id,Notification.read==False)).all():row.read=True
    db.commit();return RedirectResponse('/notifications',303)
@app.get('/account',response_class=HTMLResponse)
def account_portal(request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    workloads=db.scalars(select(Workload).where(Workload.user_id==u.id,Workload.state!=State.deleted)).all();wallet=db.scalar(select(Wallet).where(Wallet.user_id==u.id))
    if not wallet:wallet=Wallet(user_id=u.id);db.add(wallet);db.commit()
    referral=db.scalar(select(ReferralCode).where(ReferralCode.user_id==u.id));identities=db.scalars(select(AuthIdentity).where(AuthIdentity.user_id==u.id)).all();security=db.scalar(select(ApiKey).where(ApiKey.user_id==u.id,ApiKey.revoked==False));plans=db.scalars(select(PlanEvent).where(PlanEvent.user_id==u.id).order_by(PlanEvent.created_at.desc()).limit(20)).all();key_ids=db.scalars(select(ApiKey.id).where(ApiKey.user_id==u.id)).all();api_logs=db.scalars(select(ApiRequestLog).where(ApiRequestLog.api_key_id.in_(key_ids)).order_by(ApiRequestLog.created_at.desc()).limit(20)).all() if key_ids else [];logins=db.scalars(select(AuditLog).where(AuditLog.actor_id==u.id,AuditLog.action=='login').order_by(AuditLog.created_at.desc()).limit(10)).all();onboarding={'identity':bool(identities),'deployment':bool(workloads),'security':bool(security),'telegram':u.telegram_id>0}
    return templates.TemplateResponse('account.html',ctx(request,u,workloads=workloads,wallet=wallet,referral=referral,plans=plans,logins=logins,api_logs=api_logs,onboarding=onboarding))
@app.post('/account/referral/create')
def create_referral(request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    row=db.scalar(select(ReferralCode).where(ReferralCode.user_id==u.id))
    if not row:db.add(ReferralCode(user_id=u.id,code='BLZ-'+secrets.token_hex(4).upper()));db.commit()
    return RedirectResponse('/account',303)
@app.post('/account/referral/redeem')
def redeem_referral(request:Request,code:str=Form(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if db.scalar(select(ReferralRedemption).where(ReferralRedemption.referred_user_id==u.id)):raise HTTPException(400,'Referral already redeemed')
    referral=db.scalar(select(ReferralCode).where(ReferralCode.code==code.strip().upper()))
    if not referral or referral.user_id==u.id:raise HTTPException(400,'Invalid referral code')
    owner_wallet=db.scalar(select(Wallet).where(Wallet.user_id==referral.user_id)) or Wallet(user_id=referral.user_id);user_wallet=db.scalar(select(Wallet).where(Wallet.user_id==u.id)) or Wallet(user_id=u.id);db.add_all([owner_wallet,user_wallet]);owner_wallet.credits+=5;user_wallet.credits+=5;db.add(ReferralRedemption(code_id=referral.id,referred_user_id=u.id));db.add(Notification(user_id=referral.user_id,title='Referral reward',message='You earned 5 Blaze credits.'));db.commit();return RedirectResponse('/account',303)
@app.post('/admin/platform-switch')
def platform_switch(request:Request,enabled:bool=Form(False),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if u.role!=Role.owner:raise HTTPException(403)
    row=db.get(PlatformSetting,'deployments_enabled') or PlatformSetting(key='deployments_enabled',value='true');db.add(row);row.value='true' if enabled else 'false';audit(db,u,'platform.switch','platform',request.client.host if request.client else '',{'enabled':enabled});db.commit();return RedirectResponse('/admin/operations',303)
@app.post('/admin/announcements')
async def create_announcement(request:Request,title:str=Form(...),message:str=Form(...),level:str=Form('info'),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner}:raise HTTPException(403)
    if level not in {'info','warning','maintenance','critical'}:raise HTTPException(400)
    item=Announcement(title=title[:160],message=message[:5000],level=level,created_by=u.id);db.add(item);users=db.scalars(select(User).where(User.banned==False)).all()
    for target in users:db.add(Notification(user_id=target.id,title=item.title,message=item.message))
    audit(db,u,'announcement.create','platform',request.client.host if request.client else '',{'level':level});db.commit()
    from app.telegram_bot import send_user_notification
    await asyncio.gather(*(send_user_notification(target.id,f'📢 <b>{item.title}</b>\n{item.message[:1000]}') for target in users[:25]),return_exceptions=True)
    return RedirectResponse('/admin/announcements',303)
@app.post('/admin/incidents')
def create_incident(request:Request,title:str=Form(...),message:str=Form(...),impact:str=Form(...),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner} or impact not in {'minor','major','critical'}:raise HTTPException(403)
    db.add(Incident(title=title[:160],message=message[:5000],impact=impact,created_by=u.id));audit(db,u,'incident.create','platform',request.client.host if request.client else '',{'impact':impact});db.commit();return RedirectResponse('/admin/incidents',303)
@app.post('/admin/incidents/{incident_id}/resolve')
def resolve_incident(incident_id:int,request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner}:raise HTTPException(403)
    row=db.get(Incident,incident_id)
    if not row:raise HTTPException(404)
    row.status='resolved';row.resolved_at=datetime.now(timezone.utc);db.commit();return RedirectResponse('/admin/incidents',303)
@app.get('/admin/audit.csv')
def audit_export(u:User=Depends(current),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner}:raise HTTPException(403)
    output=io.StringIO();writer=csv.writer(output);writer.writerow(['id','created_at','actor_id','action','target','ip','detail'])
    for row in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10000)).all():writer.writerow([row.id,row.created_at,row.actor_id,row.action,row.target,row.ip,row.detail])
    return Response(output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="blazenxt-audit.csv"'})
@app.post('/admin/bot/repair')
async def repair_bot(request:Request,u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner}:raise HTTPException(403)
    try:await configure_telegram_webhook();await inspect_telegram_runtime(False);audit(db,u,'telegram.repair','platform',request.client.host if request.client else '');db.commit()
    except Exception as e:BOT_RUNTIME.update({'online':False,'error':str(e)[:300]});raise HTTPException(502,str(e))
    return RedirectResponse('/admin/bot',303)
@app.get('/admin',response_class=HTMLResponse)
def admin(request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner}:raise HTTPException(403)
    stats={'users':db.scalar(select(func.count()).select_from(User)) or 0,'workloads':db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0,'tickets':db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.status.in_(['open','in_progress']))) or 0,'incidents':db.scalar(select(func.count()).select_from(Incident).where(Incident.status!='resolved')) or 0};recent=db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(20)).all()
    return templates.TemplateResponse('admin_overview.html',ctx(request,u,stats=stats,recent=recent,deployments_enabled=deployments_enabled(db)))
@app.get('/admin/{section}',response_class=HTMLResponse)
def admin_section(section:str,request:Request,u:User=Depends(current),db:Session=Depends(get_db)):
    if u.role not in {Role.admin,Role.owner}:raise HTTPException(403)
    allowed={'users','tickets','announcements','incidents','audit','operations','bot'}
    if section not in allowed:raise HTTPException(404)
    data={'section':section,'users':[],'tickets':[],'announcements':[],'incidents':[],'audits':[],'deployments_enabled':deployments_enabled(db),'workload_count':db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0,'global_limit':s.global_workload_limit,'processed_updates':db.scalar(select(func.count()).select_from(ProcessedTelegramUpdate).where(ProcessedTelegramUpdate.created_at>=datetime.now(timezone.utc)-timedelta(hours=24))) or 0,'telegram_users':db.scalar(select(func.count()).select_from(User).where(User.telegram_id>0)) or 0}
    if section=='users':data['users']=db.scalars(select(User).order_by(User.created_at.desc()).limit(500)).all()
    elif section=='tickets':data['tickets']=db.scalars(select(SupportTicket).order_by(SupportTicket.created_at.desc()).limit(300)).all()
    elif section=='announcements':data['announcements']=db.scalars(select(Announcement).order_by(Announcement.created_at.desc()).limit(200)).all()
    elif section=='incidents':data['incidents']=db.scalars(select(Incident).order_by(Incident.created_at.desc()).limit(200)).all()
    elif section=='audit':data['audits']=db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500)).all()
    return templates.TemplateResponse('admin_section.html',ctx(request,u,**data))
@app.post('/admin/users/{uid}')
def update_user(uid:int,request:Request,role:str=Form(...),banned:bool=Form(False),quota_value:str=Form(''),u:User=Depends(current),_=Depends(csrf),db:Session=Depends(get_db)):
    if u.role!=Role.owner:raise HTTPException(403)
    t=db.get(User,uid)
    if not t:raise HTTPException(404)
    old_role=t.role;t.role=Role(role);t.banned=banned;t.quota=int(quota_value) if quota_value.strip() else None
    if old_role!=t.role:db.add(PlanEvent(user_id=t.id,old_plan=old_role.value,new_plan=t.role.value,changed_by=u.id));db.add(Notification(user_id=t.id,title='Plan updated',message=f'Your plan changed from {old_role.value} to {t.role.value}.'))
    audit(db,u,'user.update',f'user:{uid}',request.client.host if request.client else '');db.commit();return RedirectResponse('/admin/users',303)
@app.get('/health/live')
def live():return {'status':'ok'}
@app.get('/health/ready')
def ready(db:Session=Depends(get_db)):db.execute(select(1));return {'status':'ready','railway_configured':RailwayClient().configured,'telegram_online':BOT_RUNTIME['online']}
@app.get('/health/bot')
def bot_health():return {'online':BOT_RUNTIME['online'],'username':BOT_RUNTIME['username'],'started_at':BOT_RUNTIME['started_at'],'last_checked_at':BOT_RUNTIME['last_checked_at'],'pending_updates':BOT_RUNTIME['pending_updates'],'auto_repaired':BOT_RUNTIME['auto_repaired']}
@app.get('/metrics')
def metrics():return Response(generate_latest(),media_type=CONTENT_TYPE_LATEST)

import asyncio,hashlib,html,io,json,re,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
import httpx
from sqlalchemy import func,select
from app.config import get_settings
from app.db import SessionLocal
from app.models import Artifact,Backup,PlatformSetting,Role,State,TelegramUploadDraft,User,Workload,WorkloadMember
from app.railway import RailwayClient
from app.security import inspect_zip,safe_filename
from app.services import audit,perform_action,provision,quota
from app.webhooks import dispatch_event
s=get_settings()
async def api(method,payload=None):
    if not s.bot_token:return None
    async with httpx.AsyncClient(timeout=30) as client:
        response=await client.post(f'https://api.telegram.org/bot{s.bot_token}/{method}',json=payload or {})
    response.raise_for_status();body=response.json()
    if not body.get('ok'):raise RuntimeError(body.get('description','Telegram API error'))
    return body.get('result')
async def send(chat_id,text,keyboard=None):
    data={'chat_id':chat_id,'text':text,'parse_mode':'HTML','disable_web_page_preview':True}
    if keyboard:data['reply_markup']={'inline_keyboard':keyboard}
    return await api('sendMessage',data)
async def edit(chat_id,message_id,text,keyboard=None):
    data={'chat_id':chat_id,'message_id':message_id,'text':text,'parse_mode':'HTML','disable_web_page_preview':True}
    if keyboard:data['reply_markup']={'inline_keyboard':keyboard}
    try:return await api('editMessageText',data)
    except Exception:return await send(chat_id,text,keyboard)
def user_for(db,tg):
    tid=int(tg['id']);u=db.scalar(select(User).where(User.telegram_id==tid))
    if not u:
        name=' '.join(filter(None,[tg.get('first_name'),tg.get('last_name')])) or 'User';u=User(telegram_id=tid,username=tg.get('username'),display_name=name,role=Role.owner if tid in s.owners else Role.user);db.add(u);db.commit();db.refresh(u)
    if u.banned:raise PermissionError('Account is suspended')
    return u
def visible_workloads(db,u):
    if u.role in {Role.admin,Role.owner}:return db.scalars(select(Workload).where(Workload.state!=State.deleted).order_by(Workload.created_at.desc())).all()
    shared=db.scalars(select(WorkloadMember.workload_id).where(WorkloadMember.user_id==u.id)).all();return db.scalars(select(Workload).where((Workload.user_id==u.id)|(Workload.id.in_(shared)),Workload.state!=State.deleted).order_by(Workload.created_at.desc())).all()
def require_workload(db,u,wid,permission='view'):
    w=db.get(Workload,int(wid))
    if not w:raise PermissionError('Server not found')
    if w.user_id==u.id or u.role in {Role.admin,Role.owner}:return w
    member=db.scalar(select(WorkloadMember).where(WorkloadMember.workload_id==w.id,WorkloadMember.user_id==u.id));permissions=json.loads(member.permissions) if member else []
    if permission not in permissions:raise PermissionError('You do not have this server permission')
    return w
async def sync_workload_state(db,w):
    if not w.railway_service_id:return w
    try:
        deployments=await RailwayClient().deployments(w.railway_service_id)
        if deployments:
            status=deployments[0]['status'].upper();mapped=State.running if status=='SUCCESS' else (State.failed if status in {'FAILED','CRASHED'} else (State.stopped if status in {'REMOVED','SLEEPING'} else w.state))
            if mapped!=w.state:w.state=mapped;db.commit()
    except Exception:pass
    return w
def servers_keyboard(rows):
    buttons=[[{'text':f"{'🟢' if w.state==State.running else '⚪'} {w.name}",'callback_data':f'wl:{w.id}'}] for w in rows[:20]]
    buttons.append([{'text':'🌐 Open web panel','url':s.web_base_url}]);return buttons
def detail_text(w):
    return f"<b>{html.escape(w.name)}</b>\n\nStatus: <b>{w.state.value}</b>\nRuntime: <code>{w.runtime}</code>\nEntrypoint: <code>{html.escape(w.entrypoint)}</code>\nServer ID: <code>{w.id}</code>"
def detail_keyboard(w):return [[{'text':'▶ Start','callback_data':f'act:start:{w.id}'},{'text':'↻ Restart','callback_data':f'act:restart:{w.id}'},{'text':'■ Stop','callback_data':f'act:stop:{w.id}'}],[{'text':'📜 Logs','callback_data':f'log:{w.id}'},{'text':'💾 Backup','callback_data':f'bak:{w.id}'}],[{'text':'🖥 Open panel','url':f"{s.web_base_url.rstrip('/')}/servers/{w.id}"},{'text':'‹ Servers','callback_data':'nav:servers'}]]
async def send_user_notification(user_id,text):
    with SessionLocal() as db:
        u=db.get(User,user_id)
        if u and u.telegram_id>0:await send(u.telegram_id,text,[[{'text':'Open panel','url':s.web_base_url}]])
async def send_workload_notification(workload_id,text):
    with SessionLocal() as db:
        workload=db.get(Workload,workload_id);user=db.get(User,workload.user_id) if workload else None
        if workload and user and user.telegram_id>0:await send(user.telegram_id,text,detail_keyboard(workload))
async def provision_task(wid):
    with SessionLocal() as db:
        w=db.get(Workload,wid)
        if w:await provision(db,w)
def infer_upload_config(filename,data):
    if filename.endswith('.py'):return 'python',filename
    if filename.endswith('.js'):return 'node',filename
    names=[]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:names=[x.filename for x in archive.infolist() if not x.is_dir() and '/' not in x.filename.strip('/')]
    python_candidates=['main.py','bot.py','app.py','run.py'];node_candidates=['index.js','bot.js','app.js','server.js']
    for candidate in python_candidates:
        if candidate in names:return 'python',candidate
    for candidate in node_candidates:
        if candidate in names:return 'node',candidate
    if 'requirements.txt' in names:
        py=next((x for x in names if x.endswith('.py')),None)
        if py:return 'python',py
    if 'package.json' in names:
        js=next((x for x in names if x.endswith('.js')),None)
        if js:return 'node',js
    raise ValueError('ZIP root must contain main.py, bot.py, index.js, bot.js, requirements.txt or package.json')
def draft_text(draft):
    artifact=draft.artifact
    return f"📦 <b>Deployment preview</b>\n\nName: <b>{html.escape(draft.name)}</b>\nFile: <code>{html.escape(artifact.filename)}</code>\nSize: <b>{artifact.size/1024:.1f} KB</b>\nRuntime: <b>{draft.runtime}</b>\nEntrypoint: <code>{html.escape(draft.entrypoint)}</code>\nSHA-256: <code>{artifact.sha256[:16]}…</code>\n\nChoose runtime if detection is wrong, then press <b>Deploy & Run</b>. Draft expires in 30 minutes."
def draft_keyboard(draft):
    runtime_buttons=[]
    if not draft.artifact.filename.endswith('.js'):runtime_buttons.append({'text':('✓ ' if draft.runtime=='python' else '')+'Python','callback_data':f'upl:python:{draft.id}'})
    if not draft.artifact.filename.endswith('.py'):runtime_buttons.append({'text':('✓ ' if draft.runtime=='node' else '')+'Node.js','callback_data':f'upl:node:{draft.id}'})
    return [runtime_buttons,[{'text':'🚀 Deploy & Run','callback_data':f'upl:deploy:{draft.id}'},{'text':'✕ Cancel','callback_data':f'upl:cancel:{draft.id}'}]]
async def handle_document(message,u,db):
    doc=message['document'];filename=safe_filename(doc.get('file_name') or 'upload.zip')
    if doc.get('file_size',0)>s.max_upload_mb*1024*1024:raise ValueError(f'File exceeds {s.max_upload_mb} MB limit')
    active=db.scalar(select(func.count()).select_from(Workload).where(Workload.user_id==u.id,Workload.state!=State.deleted)) or 0;pending=db.scalar(select(func.count()).select_from(TelegramUploadDraft).where(TelegramUploadDraft.user_id==u.id,TelegramUploadDraft.status=='pending')) or 0
    switch=db.get(PlatformSetting,'deployments_enabled')
    if switch and switch.value!='true':raise ValueError('New deployments are temporarily disabled')
    if active+pending>=quota(u):raise ValueError('Workload quota reached, including pending uploads')
    file_info=await api('getFile',{'file_id':doc['file_id']});remote_path=file_info['file_path']
    async with httpx.AsyncClient(timeout=60) as client:response=await client.get(f'https://api.telegram.org/file/bot{s.bot_token}/{remote_path}')
    response.raise_for_status();data=response.content
    if len(data)>s.max_upload_mb*1024*1024:raise ValueError('Downloaded file exceeds limit')
    if filename.endswith('.zip'):inspect_zip(data)
    elif not filename.endswith(('.py','.js')):raise ValueError('Send a .py, .js or .zip file')
    runtime,entry=infer_upload_config(filename,data);opts=dict(re.findall(r'(name|entry|runtime)=([^\s]+)',message.get('caption','')))
    if opts.get('runtime') in {'python','node'}:runtime=opts['runtime']
    if opts.get('entry'):entry=safe_filename(opts['entry'])
    display=re.sub(r'[^A-Za-z0-9 _.-]','',opts.get('name',Path(filename).stem))[:80] or 'Telegram upload'
    artifact=Artifact(owner_id=u.id,filename=filename,content_type=doc.get('mime_type','application/octet-stream'),sha256=hashlib.sha256(data).hexdigest(),size=len(data),data=data);db.add(artifact);db.flush();draft=TelegramUploadDraft(user_id=u.id,artifact_id=artifact.id,name=display,runtime=runtime,entrypoint=entry,expires_at=datetime.now(timezone.utc)+timedelta(minutes=30));db.add(draft);db.commit();db.refresh(draft)
    await send(message['chat']['id'],draft_text(draft),draft_keyboard(draft))
def draft_for_user(db,u,draft_id):
    draft=db.get(TelegramUploadDraft,int(draft_id))
    if not draft or draft.user_id!=u.id or draft.status!='pending':raise ValueError('Upload draft is unavailable or already used')
    expires=draft.expires_at if draft.expires_at.tzinfo else draft.expires_at.replace(tzinfo=timezone.utc)
    if expires<datetime.now(timezone.utc):raise ValueError('Upload draft expired; send the file again')
    return draft
def runtime_entrypoint(artifact,runtime):
    if not artifact.filename.endswith('.zip'):return artifact.filename
    with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:names=[x.filename for x in archive.infolist() if not x.is_dir() and '/' not in x.filename.strip('/')]
    candidates=['main.py','bot.py','app.py','run.py'] if runtime=='python' else ['index.js','bot.js','app.js','server.js']
    return next((x for x in candidates if x in names),candidates[0])
async def deploy_draft(db,u,draft):
    active=db.scalar(select(func.count()).select_from(Workload).where(Workload.user_id==u.id,Workload.state!=State.deleted)) or 0;global_active=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0;switch=db.get(PlatformSetting,'deployments_enabled')
    if switch and switch.value!='true':raise ValueError('New deployments are temporarily disabled')
    if active>=quota(u):raise ValueError('Workload quota reached')
    if s.global_workload_limit and global_active>=s.global_workload_limit:raise ValueError('Platform capacity reached')
    workload=Workload(user_id=u.id,artifact_id=draft.artifact_id,name=draft.name,runtime=draft.runtime,entrypoint=draft.entrypoint);db.add(workload);db.flush();draft.status='deployed';audit(db,u,'workload.create.telegram',f'workload:{workload.id}','telegram',{'draft_id':draft.id,'sha256':draft.artifact.sha256});db.commit();asyncio.create_task(provision_task(workload.id));return workload
async def handle_update(update):
    callback=update.get('callback_query');message=update.get('message')
    try:
        if callback:
            try:await api('answerCallbackQuery',{'callback_query_id':callback['id']})
            except Exception:pass
            tg=callback['from'];chat=callback['message']['chat']['id'];mid=callback['message']['message_id'];data=callback.get('data','')
            with SessionLocal() as db:
                u=user_for(db,tg)
                if data.startswith('upl:'):
                    _,upload_action,draft_id=data.split(':');draft=draft_for_user(db,u,draft_id)
                    if upload_action in {'python','node'}:
                        if draft.artifact.filename.endswith('.py') and upload_action!='python':raise ValueError('Python script requires Python runtime')
                        if draft.artifact.filename.endswith('.js') and upload_action!='node':raise ValueError('JavaScript file requires Node.js runtime')
                        draft.runtime=upload_action;draft.entrypoint=runtime_entrypoint(draft.artifact,upload_action);db.commit();await edit(chat,mid,draft_text(draft),draft_keyboard(draft));return
                    if upload_action=='cancel':
                        artifact=draft.artifact;db.delete(draft);db.flush();db.delete(artifact);db.commit();await edit(chat,mid,'✕ Upload cancelled. The temporary artifact was deleted.',[[{'text':'Upload guide','callback_data':'nav:upload'}]]);return
                    if upload_action=='deploy':
                        workload=await deploy_draft(db,u,draft);await edit(chat,mid,f'🚀 <b>{html.escape(workload.name)}</b> is provisioning on Railway. You will receive a notification when it is ready.',detail_keyboard(workload));return
                    raise ValueError('Unknown upload action')
                if data=='nav:servers':
                    rows=visible_workloads(db,u);await edit(chat,mid,'<b>Your workloads</b>\nManage the same servers shown on the web panel.',servers_keyboard(rows));return
                if data=='nav:upload':
                    await edit(chat,mid,'<b>Deploy from Telegram</b>\n\nSend a <code>.py</code>, <code>.js</code> or <code>.zip</code>. BlazeNXT will validate it, detect runtime, show a preview, and wait for <b>Deploy & Run</b>.\n\nOptional caption: <code>name=MyBot runtime=python entry=main.py</code>\nDraft expires after 30 minutes.',[[{'text':'‹ Back','callback_data':'nav:servers'}]]);return
                if data.startswith('wl:'):
                    w=require_workload(db,u,data.split(':')[1]);await sync_workload_state(db,w)
                    await edit(chat,mid,detail_text(w),detail_keyboard(w));return
                if data.startswith('act:'):
                    _,action,wid=data.split(':');w=require_workload(db,u,wid,'control')
                    await perform_action(db,w,action);audit(db,u,f'workload.{action}.telegram',f'workload:{w.id}','telegram');db.commit();await dispatch_event(w.id,f'workload.{action}',{'source':'telegram'});await edit(chat,mid,detail_text(w),detail_keyboard(w));return
                if data.startswith('log:'):
                    w=require_workload(db,u,data.split(':')[1],'logs')
                    deployments=await RailwayClient().deployments(w.railway_service_id) if w.railway_service_id else [];logs=await RailwayClient().logs(deployments[0]['id']) if deployments else []
                    body='\n'.join(str(x.get('message','')) for x in logs[-30:])[-3500:] or 'No logs available.';await send(chat,f'📜 <b>{html.escape(w.name)} logs</b>\n<pre>{html.escape(body)}</pre>',detail_keyboard(w));return
                if data.startswith('bak:'):
                    w=require_workload(db,u,data.split(':')[1],'files')
                    a=w.artifact;db.add(Backup(workload_id=w.id,name='Telegram backup',filename=a.filename,sha256=a.sha256,size=a.size,data=a.data));audit(db,u,'backup.create.telegram',f'workload:{w.id}','telegram');db.commit();await send(chat,f'💾 Backup created for <b>{html.escape(w.name)}</b>.',detail_keyboard(w));return
        if not message:return
        tg=message['from'];chat=message['chat']['id'];text=message.get('text','')
        with SessionLocal() as db:
            u=user_for(db,tg)
            if 'document' in message:await handle_document(message,u,db);return
            if text.startswith('/start'):
                await send(chat,f"🔥 <b>Welcome to BlazeNXT, {html.escape(u.display_name)}</b>\n\nYour Telegram bot and web panel are fully synchronized. Upload code here or manage deployments on the web.",[[{'text':'🖥 My servers','callback_data':'nav:servers'},{'text':'🌐 Web panel','url':s.web_base_url}],[{'text':'📦 Upload guide','callback_data':'nav:upload'}]]);return
            if text.startswith('/servers'):
                rows=visible_workloads(db,u);await send(chat,'<b>Your workloads</b>',servers_keyboard(rows));return
            if text.startswith('/status'):
                rows=visible_workloads(db,u);online=sum(1 for w in rows if w.state==State.running);global_active=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0
                await send(chat,f'📊 <b>BlazeNXT status</b>\n\nAccount: <b>{u.role.value}</b>\nYour workloads: <b>{len(rows)}</b>\nOnline: <b>{online}</b>\nYour quota: <b>{quota(u)}</b>\nPlatform capacity: <b>{global_active}/{s.global_workload_limit or "∞"}</b>',[[{'text':'My servers','callback_data':'nav:servers'},{'text':'Web dashboard','url':s.web_base_url}]]);return
            if text.startswith('/account'):
                await send(chat,f'👤 <b>{html.escape(u.display_name)}</b>\n\nRole: <b>{u.role.value}</b>\nTelegram ID: <code>{u.telegram_id}</code>\nWorkload quota: <b>{quota(u)}</b>',[[{'text':'Account portal','url':f"{s.web_base_url.rstrip('/')}/account"},{'text':'Login & security','url':f"{s.web_base_url.rstrip('/')}/account/security"}]]);return
            if text.startswith('/help') or text.startswith('/deploy'):
                await send(chat,'<b>Deploy from Telegram</b>\n\n1. Send a <code>.py</code>, <code>.js</code> or <code>.zip</code> document.\n2. Review detected runtime and entrypoint.\n3. Select Python or Node.js if needed.\n4. Press <b>Deploy & Run</b>.\n\nOptional caption:\n<code>name=MyBot runtime=python entry=main.py</code>\n\nDrafts expire in 30 minutes. Configure secrets only in the secure web panel.');return
            await send(chat,'Use /servers to manage workloads or send /help for the upload guide.')
    except Exception as e:
        target=(callback or message or {}).get('message',message or {});chat=(target.get('chat') or {}).get('id')
        if chat:
            try:await send(chat,f'❌ {html.escape(str(e)[:300])}')
            except Exception:pass

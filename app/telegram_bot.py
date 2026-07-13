import asyncio,hashlib,html,json,re
from pathlib import Path
import httpx
from sqlalchemy import func,select
from app.config import get_settings
from app.db import SessionLocal
from app.models import Artifact,Backup,PlatformSetting,Role,State,User,Workload,WorkloadMember
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
async def provision_task(wid):
    with SessionLocal() as db:
        w=db.get(Workload,wid)
        if w:await provision(db,w)
async def handle_document(message,u,db):
    doc=message['document'];name=safe_filename(doc.get('file_name') or 'upload.zip')
    if doc.get('file_size',0)>s.max_upload_mb*1024*1024:raise ValueError(f'File exceeds {s.max_upload_mb} MB limit')
    active=db.scalar(select(func.count()).select_from(Workload).where(Workload.user_id==u.id,Workload.state!=State.deleted)) or 0
    switch=db.get(PlatformSetting,'deployments_enabled')
    if switch and switch.value!='true':raise ValueError('New deployments are temporarily disabled')
    global_active=db.scalar(select(func.count()).select_from(Workload).where(Workload.state!=State.deleted)) or 0
    if s.global_workload_limit and global_active>=s.global_workload_limit:raise ValueError('Platform capacity reached. Contact the administrator.')
    if active>=quota(u):raise ValueError('Workload quota reached')
    caption=message.get('caption','');opts=dict(re.findall(r'(name|entry|runtime)=([^\s]+)',caption))
    runtime=opts.get('runtime') or ('node' if name.endswith('.js') else 'python')
    if runtime not in {'python','node'}:raise ValueError('runtime must be python or node')
    entry=opts.get('entry') or (name if not name.endswith('.zip') else ('index.js' if runtime=='node' else 'main.py'));entry=safe_filename(entry)
    file_info=await api('getFile',{'file_id':doc['file_id']});path=file_info['file_path']
    async with httpx.AsyncClient(timeout=60) as client:r=await client.get(f'https://api.telegram.org/file/bot{s.bot_token}/{path}')
    r.raise_for_status();data=r.content
    if len(data)>s.max_upload_mb*1024*1024:raise ValueError('Downloaded file exceeds limit')
    if name.endswith('.zip'):inspect_zip(data)
    elif not ((runtime=='python' and name.endswith('.py')) or (runtime=='node' and name.endswith('.js'))):raise ValueError('Send a .py, .js or .zip file')
    display=opts.get('name',Path(name).stem)[:80];a=Artifact(owner_id=u.id,filename=name,content_type=doc.get('mime_type','application/octet-stream'),sha256=hashlib.sha256(data).hexdigest(),size=len(data),data=data);db.add(a);db.flush();w=Workload(user_id=u.id,artifact_id=a.id,name=display,runtime=runtime,entrypoint=entry);db.add(w);db.flush();audit(db,u,'workload.create.telegram',f'workload:{w.id}','telegram',{'sha256':a.sha256});db.commit();asyncio.create_task(provision_task(w.id))
    await send(message['chat']['id'],f'📦 <b>{html.escape(display)}</b> accepted. Creating an isolated Railway service…',detail_keyboard(w))
async def handle_update(update):
    callback=update.get('callback_query');message=update.get('message')
    try:
        if callback:
            try:await api('answerCallbackQuery',{'callback_query_id':callback['id']})
            except Exception:pass
            tg=callback['from'];chat=callback['message']['chat']['id'];mid=callback['message']['message_id'];data=callback.get('data','')
            with SessionLocal() as db:
                u=user_for(db,tg)
                if data=='nav:servers':
                    rows=visible_workloads(db,u);await edit(chat,mid,'<b>Your workloads</b>\nManage the same servers shown on the web panel.',servers_keyboard(rows));return
                if data=='nav:upload':
                    await edit(chat,mid,'<b>Deploy from Telegram</b>\n\nSend a <code>.py</code>, <code>.js</code> or <code>.zip</code> document. Optional caption:\n<code>name=MyBot runtime=python entry=main.py</code>\n\nConfigure secrets safely in the web panel.',[[{'text':'‹ Back','callback_data':'nav:servers'}]]);return
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
                await send(chat,'<b>Deploy from Telegram</b>\n\nSend a <code>.py</code>, <code>.js</code> or <code>.zip</code> document. Optional caption:\n<code>name=MyBot runtime=python entry=main.py</code>\n\nSecrets and environment variables must be configured securely in the web panel.');return
            await send(chat,'Use /servers to manage workloads or send /help for the upload guide.')
    except Exception as e:
        target=(callback or message or {}).get('message',message or {});chat=(target.get('chat') or {}).get('id')
        if chat:
            try:await send(chat,f'❌ {html.escape(str(e)[:300])}')
            except Exception:pass

import html,json,secrets
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from app.config import get_settings
from app.models import AuditLog,PlatformSetting,Role,RunnerToken,State,WorkloadAllocation,WorkloadVariable
from app.notifications import emit
from app.railway import RailwayClient
from app.security import decrypt_secret,hash_token
s=get_settings(); QUOTAS={Role.user:2,Role.premium:20,Role.admin:100,Role.owner:1000}
def quota(u):return u.quota if u.quota is not None else QUOTAS[u.role]
def audit(db,u,action,target,ip,detail=None):db.add(AuditLog(actor_id=u.id if u else None,action=action,target=target,ip=ip,detail=json.dumps(detail or {},separators=(',',':'))))
def notify(db,user_id,event,title,message):emit(db,user_id,event,title,message,telegram=False)
def workload_variables(db,wid):
    rows=db.scalars(select(WorkloadVariable).where(WorkloadVariable.workload_id==wid)).all();return {x.name:decrypt_secret(x.encrypted_value) for x in rows}
def issue_runner_token(db,w):
    raw=secrets.token_urlsafe(32);db.add(RunnerToken(workload_id=w.id,token_hash=hash_token(raw),expires_at=datetime.now(timezone.utc)+timedelta(seconds=s.runner_token_ttl_seconds)));return raw
async def provision(db,w):
    switch=db.get(PlatformSetting,'deployments_enabled')
    if switch and switch.value!='true':
        w.state=State.failed;w.last_error='Deployments are disabled by an administrator';db.commit();return
    w.state=State.provisioning;w.last_error=None;db.commit();raw=issue_runner_token(db,w);db.commit();variables={'CONTROL_PLANE_URL':s.web_base_url.rstrip('/'),'RUNNER_TOKEN':raw,'WORKLOAD_ID':str(w.id),'ENTRYPOINT':w.entrypoint,'RUNTIME':w.runtime,**workload_variables(db,w.id)};client=RailwayClient();service_name=f'blaze-{w.user_id}-{w.id}';phase='service discovery'
    try:
        if not w.railway_service_id:
            existing=await client.find_service(service_name)
            phase='service creation'
            service_id=existing['id'] if existing else await client.create_image_service(service_name,s.railway_runner_image)
            w.railway_service_id=service_id;db.commit()
        phase='environment synchronization';await client.upsert_variables(w.railway_service_id,variables)
        allocation=db.scalar(select(WorkloadAllocation).where(WorkloadAllocation.workload_id==w.id))
        if not allocation:allocation=WorkloadAllocation(workload_id=w.id,cpu_vcpus=str(s.default_cpu_vcpus),memory_mb=s.default_memory_mb);db.add(allocation);db.commit()
        # Resource-limit API compatibility must not turn an otherwise deployable service into a failed workload.
        try:await client.update_limits(w.railway_service_id,float(allocation.cpu_vcpus),allocation.memory_mb)
        except Exception:pass
        try:await client.update_instance(w.railway_service_id,allocation.replicas,allocation.restart_policy,allocation.restart_retries)
        except Exception:pass
        phase='deployment trigger';await client.redeploy(w.railway_service_id);w.state=State.running;w.last_error=None;notify(db,w.user_id,'deployment.completed','Deployment online',f'{w.name} was accepted by Railway and started successfully.')
    except Exception as e:
        w.state=State.failed;w.last_error=f'{phase}: {str(e)[:850]}';notify(db,w.user_id,'deployment.failed','Deployment failed',f'{w.name}: {w.last_error}')
    db.commit()
    try:
        from app.webhooks import dispatch_event
        await dispatch_event(w.id,'deployment.completed' if w.state==State.running else 'deployment.failed',{'state':w.state.value,'error':w.last_error})
    except Exception:pass
    try:
        from app.telegram_bot import send_workload_notification
        await send_workload_notification(w.id,f"{'✅' if w.state==State.running else '❌'} <b>{w.name}</b> is now <b>{w.state.value}</b>."+(f"\n<code>{html.escape(w.last_error)}</code>" if w.last_error else ''))
    except Exception:pass
async def refresh_artifact(db,w):
    raw=issue_runner_token(db,w);db.commit();variables={'CONTROL_PLANE_URL':s.web_base_url.rstrip('/'),'RUNNER_TOKEN':raw,'WORKLOAD_ID':str(w.id),'ENTRYPOINT':w.entrypoint,'RUNTIME':w.runtime,**workload_variables(db,w.id)}
    await RailwayClient().upsert_variables(w.railway_service_id,variables);await RailwayClient().redeploy(w.railway_service_id);w.state=State.running;w.last_error=None;db.commit()
async def perform_action(db,w,action):
    switch=db.get(PlatformSetting,'deployments_enabled')
    if switch and switch.value!='true' and action in {'start','restart'}:raise ValueError('Power-on operations are disabled by an administrator')
    allocation=db.scalar(select(WorkloadAllocation).where(WorkloadAllocation.workload_id==w.id))
    if allocation and allocation.suspended and action!='delete':raise ValueError('Workload is suspended by an administrator')
    client=RailwayClient()
    if not w.railway_service_id:raise ValueError('Workload has no Railway service yet')
    if action in {'start','restart'}:await client.redeploy(w.railway_service_id);w.state=State.running
    elif action=='stop':
        deployments=await client.deployments(w.railway_service_id)
        if deployments:await client.stop(deployments[0]['id'])
        w.state=State.stopped
    elif action=='delete':await client.delete(w.railway_service_id);w.state=State.deleted
    else:raise ValueError('Unknown action')
    w.last_error=None;db.commit();return w

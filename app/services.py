import json,secrets
from datetime import datetime,timedelta,timezone
from app.config import get_settings
from app.models import AuditLog,Role,RunnerToken,State
from app.railway import RailwayClient
from app.security import hash_token
s=get_settings(); QUOTAS={Role.user:2,Role.premium:20,Role.admin:100,Role.owner:1000}
def quota(u):return u.quota if u.quota is not None else QUOTAS[u.role]
def audit(db,u,action,target,ip,detail=None):db.add(AuditLog(actor_id=u.id if u else None,action=action,target=target,ip=ip,detail=json.dumps(detail or {})))
async def provision(db,w):
    w.state=State.provisioning;db.commit();raw=secrets.token_urlsafe(32)
    db.add(RunnerToken(workload_id=w.id,token_hash=hash_token(raw),expires_at=datetime.now(timezone.utc)+timedelta(seconds=s.runner_token_ttl_seconds)));db.commit()
    variables={'CONTROL_PLANE_URL':s.web_base_url.rstrip('/'),'RUNNER_TOKEN':raw,'WORKLOAD_ID':str(w.id),'ENTRYPOINT':w.entrypoint,'RUNTIME':w.runtime}
    try:w.railway_service_id=await RailwayClient().create(f'blaze-{w.user_id}-{w.id}',variables);w.state=State.running;w.last_error=None
    except Exception as e:w.state=State.failed;w.last_error=str(e)[:1000]
    db.commit()

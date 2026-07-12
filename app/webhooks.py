import hashlib,hmac,ipaddress,json,socket
from datetime import datetime,timezone
from urllib.parse import urlparse
import httpx
from sqlalchemy import select
from app.db import SessionLocal
from app.models import WebhookDelivery,WorkloadWebhook
from app.security import decrypt_secret

def validate_webhook_url(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Webhook must use standard HTTPS')
    for item in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM):
        ip=ipaddress.ip_address(item[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:raise ValueError('Private or reserved webhook destinations are blocked')
    return url
async def dispatch_event(workload_id,event,data=None):
    with SessionLocal() as db:
        hooks=db.scalars(select(WorkloadWebhook).where(WorkloadWebhook.workload_id==workload_id,WorkloadWebhook.enabled==True)).all()
        for hook in hooks:
            allowed=json.loads(hook.events)
            if '*' not in allowed and event not in allowed:continue
            payload={'event':event,'workload_id':workload_id,'created_at':datetime.now(timezone.utc).isoformat(),'data':data or {}};body=json.dumps(payload,separators=(',',':')).encode();secret=decrypt_secret(hook.encrypted_secret);signature=hmac.new(secret.encode(),body,hashlib.sha256).hexdigest();status=None;error=None;success=False
            try:
                validate_webhook_url(hook.url)
                async with httpx.AsyncClient(timeout=10,follow_redirects=False) as client:response=await client.post(hook.url,content=body,headers={'Content-Type':'application/json','User-Agent':'BlazeNXT-Webhooks/1.0','X-BlazeNXT-Event':event,'X-BlazeNXT-Signature-256':f'sha256={signature}'})
                status=response.status_code;success=200<=status<300
                if not success:error=f'HTTP {status}'
            except Exception as e:error=str(e)[:500]
            db.add(WebhookDelivery(webhook_id=hook.id,event=event,status_code=status,success=success,error=error));db.commit()

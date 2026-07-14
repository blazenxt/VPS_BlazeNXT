import asyncio,html,json,smtplib
from datetime import datetime,timedelta,timezone
from email.message import EmailMessage
from sqlalchemy import select
from pywebpush import WebPushException,webpush
from app.config import get_settings
from app.db import SessionLocal
from app.models import AuthIdentity,DeliveryOutbox,Notification,NotificationPreference,PushSubscription,User
s=get_settings();CATEGORIES={'deployment','security','billing','support','incident'}
def preference_for(db,user_id):return db.scalar(select(NotificationPreference).where(NotificationPreference.user_id==user_id))
def emit(db,user_id,event,title,message,telegram=True):
    category=event.split('.',1)[0];pref=preference_for(db,user_id);categories=set(json.loads(pref.event_categories)) if pref else CATEGORIES
    if category not in categories and category!='security':return
    web_enabled=not pref or pref.web_enabled or category=='security';email_enabled=(not pref or pref.email_enabled) and bool(s.smtp_host and s.smtp_from);telegram_enabled=not pref or pref.telegram_enabled;push_enabled=(not pref or pref.push_enabled) and bool(s.vapid_public_key and s.vapid_private_key)
    if web_enabled:db.add(Notification(user_id=user_id,title=title[:120],message=message[:5000]))
    if email_enabled:db.add(DeliveryOutbox(user_id=user_id,channel='email',event=event,title=title[:160],message=message[:5000]))
    if push_enabled and db.scalar(select(PushSubscription.id).where(PushSubscription.user_id==user_id,PushSubscription.enabled==True).limit(1)):db.add(DeliveryOutbox(user_id=user_id,channel='push',event=event,title=title[:160],message=message[:5000]))
    user=db.get(User,user_id)
    if telegram and telegram_enabled and user and user.telegram_id>0:db.add(DeliveryOutbox(user_id=user_id,channel='telegram',event=event,title=title[:160],message=message[:5000]))
def primary_email(db,user_id):
    identities=db.scalars(select(AuthIdentity).where(AuthIdentity.user_id==user_id,AuthIdentity.email.is_not(None)).order_by(AuthIdentity.last_login_at.desc())).all();return next((x.email for x in identities if x.email),None)
def email_message(to,title,message,event):
    mail=EmailMessage();mail['Subject']=f'BlazeNXT · {title}';mail['From']=s.smtp_from;mail['To']=to;mail.set_content(f'{title}\n\n{message}\n\nEvent: {event}\nOpen BlazeNXT: {s.web_base_url}\n\nThis automated message follows your notification preferences.')
    safe_title=html.escape(title);safe_message=html.escape(message).replace('\n','<br>');safe_event=html.escape(event);safe_url=html.escape(s.web_base_url,quote=True);mail.add_alternative(f'''<!doctype html><html><body style="margin:0;background:#080a0e;color:#edf1f8;font-family:Arial,sans-serif"><div style="max-width:580px;margin:30px auto;background:#11151d;border:1px solid #252b38;border-radius:10px;padding:28px"><div style="color:#ff6a3d;font-size:12px;font-weight:bold">BLAZENXT v1</div><h1 style="font-size:24px">{safe_title}</h1><p style="color:#aab2c1;line-height:1.6">{safe_message}</p><a href="{safe_url}" style="display:inline-block;background:#ff6a3d;color:white;text-decoration:none;padding:10px 16px;border-radius:6px;font-weight:bold">Open control panel</a><p style="margin-top:25px;color:#687184;font-size:11px">Event: {safe_event}</p></div></body></html>''',subtype='html');return mail
def send_email(to,title,message,event):
    mail=email_message(to,title,message,event)
    with smtplib.SMTP(s.smtp_host,s.smtp_port,timeout=20) as server:
        if s.smtp_starttls:server.starttls()
        if s.smtp_username:server.login(s.smtp_username,s.smtp_password)
        server.send_message(mail)
def send_push(subscription,title,message,event):
    payload=json.dumps({'title':title,'body':message[:500],'event':event,'url':s.web_base_url+'/notifications','icon':'/static/pwa-192.png','badge':'/static/blazenxt-favicon.png'},separators=(',',':'))
    return webpush(subscription_info={'endpoint':subscription.endpoint,'keys':{'p256dh':subscription.p256dh,'auth':subscription.auth}},data=payload,vapid_private_key=s.vapid_private_key,vapid_claims={'sub':s.vapid_subject})
async def process_outbox(limit=30):
    with SessionLocal() as db:
        now=datetime.now(timezone.utc);rows=db.scalars(select(DeliveryOutbox).where(DeliveryOutbox.status=='pending',DeliveryOutbox.next_attempt<=now,DeliveryOutbox.attempts<5).order_by(DeliveryOutbox.created_at).limit(limit)).all()
        for row in rows:
            try:
                if row.channel=='email':
                    address=primary_email(db,row.user_id)
                    if not address:raise RuntimeError('No verified email identity')
                    await asyncio.to_thread(send_email,address,row.title,row.message,row.event)
                elif row.channel=='telegram':
                    from app.telegram_bot import send_user_notification
                    await send_user_notification(row.user_id,f'🔔 <b>{html.escape(row.title)}</b>\n{html.escape(row.message)}')
                elif row.channel=='push':
                    subscriptions=db.scalars(select(PushSubscription).where(PushSubscription.user_id==row.user_id,PushSubscription.enabled==True)).all();delivered=0;errors=[]
                    for subscription in subscriptions:
                        try:await asyncio.to_thread(send_push,subscription,row.title,row.message,row.event);subscription.last_success_at=now;subscription.last_error=None;delivered+=1
                        except WebPushException as exc:
                            status=getattr(getattr(exc,'response',None),'status_code',None);subscription.last_error=str(exc)[:500]
                            if status in {404,410}:subscription.enabled=False
                            else:errors.append(str(exc))
                    if not delivered:raise RuntimeError(errors[0] if errors else 'No active push subscription')
                else:raise RuntimeError('Unsupported delivery channel')
                row.status='sent';row.sent_at=now;row.last_error=None
            except Exception as e:
                row.attempts+=1;row.last_error=str(e)[:500]
                if row.attempts>=5:row.status='failed'
                else:row.next_attempt=now+timedelta(minutes=2**row.attempts)
            db.commit()

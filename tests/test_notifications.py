import asyncio,json
from sqlalchemy import select
from app.db import SessionLocal
from app.models import AuthIdentity,DeliveryOutbox,Notification,NotificationPreference,User
from app.notifications import emit,process_outbox,s
import app.notifications as notification_module

def test_email_outbox_delivery(monkeypatch):
    sent=[];monkeypatch.setattr(s,'smtp_host','smtp.example.com');monkeypatch.setattr(s,'smtp_from','BlazeNXT <test@example.com>');monkeypatch.setattr(notification_module,'send_email',lambda to,title,message,event:sent.append((to,title,event)))
    with SessionLocal() as db:
        user=User(telegram_id=-661001,display_name='Email User');db.add(user);db.flush();db.add(AuthIdentity(user_id=user.id,provider='email',subject='notify@example.com',email='notify@example.com'));emit(db,user.id,'deployment.completed','Deployment online','Bot is ready');db.commit();uid=user.id
    asyncio.run(process_outbox())
    with SessionLocal() as db:
        assert db.scalar(select(Notification).where(Notification.user_id==uid));row=db.scalar(select(DeliveryOutbox).where(DeliveryOutbox.user_id==uid,DeliveryOutbox.channel=='email'));assert row.status=='sent'
    assert sent==[('notify@example.com','Deployment online','deployment.completed')]
def test_preferences_disable_optional_channels(monkeypatch):
    monkeypatch.setattr(s,'smtp_host','smtp.example.com');monkeypatch.setattr(s,'smtp_from','test@example.com')
    with SessionLocal() as db:
        user=User(telegram_id=661002,display_name='Preference User');db.add(user);db.flush();db.add(NotificationPreference(user_id=user.id,web_enabled=True,email_enabled=False,telegram_enabled=False,event_categories=json.dumps(['deployment'])));emit(db,user.id,'deployment.restart','Restarted','Done');db.commit();uid=user.id
    with SessionLocal() as db:
        assert db.scalar(select(Notification).where(Notification.user_id==uid));assert not db.scalar(select(DeliveryOutbox).where(DeliveryOutbox.user_id==uid))
def test_security_web_notification_is_mandatory():
    with SessionLocal() as db:
        user=User(telegram_id=-661003,display_name='Security User');db.add(user);db.flush();db.add(NotificationPreference(user_id=user.id,web_enabled=False,email_enabled=False,telegram_enabled=False,event_categories='[]'));emit(db,user.id,'security.test','Security test','Important');db.commit();uid=user.id
    with SessionLocal() as db:assert db.scalar(select(Notification).where(Notification.user_id==uid))

import asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
import app.notifications as notification_module
from app.db import SessionLocal
from app.main import app,s
from app.models import DeliveryOutbox,NotificationPreference,PushSubscription,User
from app.notifications import emit,process_outbox
from app.security import read_session,sign_session

def test_push_subscribe_and_unsubscribe(monkeypatch):
    monkeypatch.setattr(s,'vapid_public_key','B'+'A'*86);monkeypatch.setattr(s,'vapid_private_key','private-test-key')
    with SessionLocal() as db:user=User(telegram_id=-771001,display_name='Push User');db.add(user);db.commit();db.refresh(user);uid=user.id
    cookie=sign_session(uid,'email');csrf=read_session(cookie)['csrf'];payload={'endpoint':'https://push.example.test/subscription/1','keys':{'p256dh':'p'*80,'auth':'a'*24}}
    with TestClient(app) as client:
        client.cookies.set('blaze_session',cookie);config=client.get('/api/push/config');assert config.json()['enabled'] is True
        response=client.post('/api/push/subscribe',json=payload,headers={'X-CSRF-Token':csrf});assert response.status_code==200
        with SessionLocal() as db:row=db.scalar(select(PushSubscription).where(PushSubscription.user_id==uid));assert row and row.enabled
        response=client.post('/api/push/unsubscribe',json={'endpoint':payload['endpoint']},headers={'X-CSRF-Token':csrf});assert response.status_code==200
        with SessionLocal() as db:assert db.scalar(select(PushSubscription).where(PushSubscription.user_id==uid)).enabled is False
def test_push_outbox_delivery(monkeypatch):
    monkeypatch.setattr(notification_module.s,'vapid_public_key','B'+'A'*86);monkeypatch.setattr(notification_module.s,'vapid_private_key','private-test-key');sent=[];monkeypatch.setattr(notification_module,'send_push',lambda sub,title,message,event:sent.append((sub.endpoint,title,event)))
    with SessionLocal() as db:
        user=User(telegram_id=-771002,display_name='Push Delivery');db.add(user);db.flush();db.add(NotificationPreference(user_id=user.id,push_enabled=True,email_enabled=False,telegram_enabled=False));db.add(PushSubscription(user_id=user.id,endpoint='https://push.example.test/subscription/2',p256dh='p'*80,auth='a'*24));db.flush();emit(db,user.id,'deployment.completed','Bot online','Ready');db.commit();uid=user.id
    asyncio.run(process_outbox())
    with SessionLocal() as db:delivery=db.scalar(select(DeliveryOutbox).where(DeliveryOutbox.user_id==uid,DeliveryOutbox.channel=='push'));assert delivery.status=='sent'
    assert sent==[('https://push.example.test/subscription/2','Bot online','deployment.completed')]
def test_service_worker_handles_push_and_click():
    source=open('static/service-worker.js').read();assert "addEventListener('push'" in source and "addEventListener('notificationclick'" in source

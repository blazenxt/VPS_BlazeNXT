from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db import SessionLocal
from app.main import app
from app.models import AuthIdentity,AuthIdentityBlock,User,UserSessionPolicy
from app.security import read_session,sign_session

def create_user_with_identities(base):
    with SessionLocal() as db:
        user=User(telegram_id=base,display_name=f'Unlink {base}');db.add(user);db.flush();db.add_all([AuthIdentity(user_id=user.id,provider='telegram',subject=str(base),display_name='Telegram'),AuthIdentity(user_id=user.id,provider='google',subject=f'g-{base}',email=f'{base}@example.com',display_name='Google')]);db.commit();return user.id
def test_unlink_identity_blocks_relink_and_revokes_sessions():
    uid=create_user_with_identities(880001);cookie=sign_session(uid,'telegram');csrf=read_session(cookie)['csrf']
    with TestClient(app) as client:
        client.cookies.set('blaze_session',cookie)
        with SessionLocal() as db:identity=db.scalar(select(AuthIdentity).where(AuthIdentity.user_id==uid,AuthIdentity.provider=='telegram'));identity_id=identity.id
        response=client.post(f'/account/identities/{identity_id}/unlink',data={'token':csrf,'confirmation':'UNLINK','code':''},follow_redirects=False)
        assert response.status_code==303
        with SessionLocal() as db:
            user=db.get(User,uid);assert user.telegram_id<0
            assert db.scalar(select(AuthIdentityBlock).where(AuthIdentityBlock.user_id==uid,AuthIdentityBlock.provider=='telegram'))
            assert db.scalar(select(UserSessionPolicy).where(UserSessionPolicy.user_id==uid))
        assert client.get('/dashboard').status_code==401
def test_last_identity_cannot_be_unlinked():
    with SessionLocal() as db:
        user=User(telegram_id=-880002,display_name='Last Identity');db.add(user);db.flush();identity=AuthIdentity(user_id=user.id,provider='email',subject='last@example.com',email='last@example.com');db.add(identity);db.commit();uid=user.id;identity_id=identity.id
    cookie=sign_session(uid,'email');csrf=read_session(cookie)['csrf']
    with TestClient(app) as client:
        client.cookies.set('blaze_session',cookie);response=client.post(f'/account/identities/{identity_id}/unlink',data={'token':csrf,'confirmation':'UNLINK','code':''})
        assert response.status_code==400

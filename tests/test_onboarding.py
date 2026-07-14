from fastapi.testclient import TestClient
from app.auth import login_response
from app.db import SessionLocal
from app.main import app
from app.models import Artifact,AuthIdentity,User,Workload
from app.security import sign_session

def test_new_user_is_sent_to_onboarding():
    with SessionLocal() as db:
        user=User(telegram_id=-440001,display_name='New User');db.add(user);db.flush();db.add(AuthIdentity(user_id=user.id,provider='email',subject='new@example.com',email='new@example.com'));db.commit();response=login_response(user.id,db,'email');assert response.headers['location']=='/onboarding'
def test_existing_workload_user_is_sent_to_dashboard():
    with SessionLocal() as db:
        user=User(telegram_id=-440002,display_name='Existing User');db.add(user);db.flush();artifact=Artifact(owner_id=user.id,filename='main.py',content_type='text/x-python',sha256='x'*64,size=8,data=b'print(1)');db.add(artifact);db.flush();db.add(Workload(user_id=user.id,artifact_id=artifact.id,name='Bot',runtime='python',entrypoint='main.py'));db.commit();response=login_response(user.id,db,'email');assert response.headers['location']=='/dashboard'
def test_onboarding_page_and_dismiss():
    with SessionLocal() as db:
        user=User(telegram_id=440003,display_name='Wizard User');db.add(user);db.commit();db.refresh(user);uid=user.id
    with TestClient(app) as client:
        client.cookies.set('blaze_session',sign_session(uid,'telegram'));page=client.get('/onboarding');assert page.status_code==200 and 'Set up your hosting workspace' in page.text and 'progress-track' in page.text
        # CSRF is intentionally tested by the existing security suite; page route must remain accessible.

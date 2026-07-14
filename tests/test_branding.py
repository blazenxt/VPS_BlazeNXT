import io
from PIL import Image
from fastapi.testclient import TestClient
from app.branding import invalidate_brand
from app.db import SessionLocal
from app.main import app
from app.models import Role,User
from app.security import read_session,sign_session

def png_bytes():
    out=io.BytesIO();Image.new('RGB',(80,80),(255,80,20)).save(out,'PNG');return out.getvalue()
def test_owner_can_customize_and_reset_branding():
    with SessionLocal() as db:
        user=User(telegram_id=330001,display_name='Brand Owner',role=Role.owner);db.add(user);db.commit();db.refresh(user);uid=user.id
    cookie=sign_session(uid,'telegram');csrf=read_session(cookie)['csrf']
    with TestClient(app) as client:
        client.cookies.set('blaze_session',cookie)
        data={'token':csrf,'name':'NovaHost','tagline':'BOT CLOUD · v1','landing_kicker':'Secure cloud hosting','landing_title':'Deploy faster.','landing_accent':'Operate clearly.','landing_subtitle':'A custom hosting control plane.','footer_text':'Custom infrastructure control.','primary_color':'#123456','accent_color':'#abcdef'}
        response=client.post('/admin/branding',data=data,follow_redirects=False);assert response.status_code==303
        invalidate_brand();page=client.get('/dashboard');assert 'NovaHost' in page.text and '--orange:#123456' in page.text
        logo=client.post('/admin/branding/logo',data={'token':csrf},files={'file':('logo.png',png_bytes(),'image/png')},follow_redirects=False);assert logo.status_code==303
        image=client.get('/brand/logo');assert image.status_code==200 and image.headers['content-type']=='image/png'
        reset=client.post('/admin/branding/reset',data={'token':csrf},follow_redirects=False);assert reset.status_code==303
        invalidate_brand();assert 'BlazeNXT' in client.get('/dashboard').text

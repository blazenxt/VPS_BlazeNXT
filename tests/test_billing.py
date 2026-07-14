import io
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db import SessionLocal
from app.main import app,s
from app.models import BillingInvoice,BillingPlan,PaymentRequest,Role,Subscription,User
from app.security import read_session,sign_session

def proof_png():
    out=io.BytesIO();Image.new('RGB',(120,80),(230,230,230)).save(out,'PNG');return out.getvalue()
def test_manual_upi_submit_approve_and_invoice(monkeypatch):
    monkeypatch.setattr(s,'billing_enabled',True);monkeypatch.setattr(s,'billing_upi_id','merchant@upi');monkeypatch.setattr(s,'billing_payee_name','Test Host')
    with SessionLocal() as db:
        customer=User(telegram_id=-881001,display_name='Billing Customer');owner=User(telegram_id=881002,display_name='Billing Owner',role=Role.owner);db.add_all([customer,owner]);db.commit();db.refresh(customer);db.refresh(owner);customer_id=customer.id;owner_id=owner.id
    customer_cookie=sign_session(customer_id,'email');customer_csrf=read_session(customer_cookie)['csrf']
    with TestClient(app) as client:
        client.cookies.set('blaze_session',customer_cookie);store=client.get('/store');assert store.status_code==200 and 'Submit UPI payment' in store.text
        with SessionLocal() as db:plan=db.scalar(select(BillingPlan).where(BillingPlan.code=='premium-monthly'));plan_id=plan.id
        qr=client.get(f'/billing/qr/{plan_id}');assert qr.status_code==200 and qr.headers['content-type']=='image/png'
        payment=client.post('/billing/payments',data={'token':customer_csrf,'plan_id':plan_id,'transaction_reference':'UTR881001'},files={'proof':('proof.png',proof_png(),'image/png')},follow_redirects=False);assert payment.status_code==303
        duplicate=client.post('/billing/payments',data={'token':customer_csrf,'plan_id':plan_id,'transaction_reference':'UTR881001'},files={'proof':('proof.png',proof_png(),'image/png')});assert duplicate.status_code==409
        with SessionLocal() as db:request=db.scalar(select(PaymentRequest).where(PaymentRequest.user_id==customer_id));payment_id=request.id
        owner_cookie=sign_session(owner_id,'telegram');owner_csrf=read_session(owner_cookie)['csrf'];client.cookies.set('blaze_session',owner_cookie)
        approval=client.post(f'/admin/billing/payments/{payment_id}/approve',data={'token':owner_csrf},follow_redirects=False);assert approval.status_code==303
        with SessionLocal() as db:
            customer=db.get(User,customer_id);assert customer.role==Role.premium
            assert db.scalar(select(Subscription).where(Subscription.user_id==customer_id,Subscription.active==True))
            invoice=db.scalar(select(BillingInvoice).where(BillingInvoice.user_id==customer_id));assert invoice.invoice_number.startswith('BLZ-')
        client.cookies.set('blaze_session',customer_cookie);invoice_page=client.get(f'/billing/invoices/{invoice.id}');assert invoice_page.status_code==200 and invoice.invoice_number in invoice_page.text
def test_billing_disabled_blocks_payment(monkeypatch):
    monkeypatch.setattr(s,'billing_enabled',False);monkeypatch.setattr(s,'billing_upi_id','')
    with SessionLocal() as db:user=User(telegram_id=-881003,display_name='Disabled Billing');db.add(user);db.commit();db.refresh(user);uid=user.id
    cookie=sign_session(uid,'email');csrf=read_session(cookie)['csrf']
    with TestClient(app) as client:
        client.cookies.set('blaze_session',cookie);response=client.post('/billing/payments',data={'token':csrf,'plan_id':1,'transaction_reference':'UTR881003'},files={'proof':('proof.png',proof_png(),'image/png')});assert response.status_code==503

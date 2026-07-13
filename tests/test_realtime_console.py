from datetime import datetime,timezone
from fastapi.testclient import TestClient
from app.db import SessionLocal
from app.main import app
from app.models import Artifact,HealthSnapshot,User,Workload,WorkloadAllocation
from app.railway import RailwayClient
from app.security import sign_session

def seed_runtime(base):
    with SessionLocal() as db:
        user=User(telegram_id=base,display_name='Metrics User');db.add(user);db.flush();artifact=Artifact(owner_id=user.id,filename='main.py',content_type='text/x-python',sha256='x'*64,size=8,data=b'print(1)');db.add(artifact);db.flush();workload=Workload(user_id=user.id,artifact_id=artifact.id,name='Metrics Bot',runtime='python',entrypoint='main.py',railway_service_id=f'service-{base}');db.add(workload);db.flush();db.add(WorkloadAllocation(workload_id=workload.id,cpu_vcpus='1.0',memory_mb=1024,replicas=2));db.add(HealthSnapshot(workload_id=workload.id,state='running',created_at=datetime.now(timezone.utc)));db.commit();return user.id,workload.id
def test_log_download_and_honest_metrics(monkeypatch):
    uid,wid=seed_runtime(770001)
    async def deployments(self,sid):return [{'id':'dep-1','status':'SUCCESS','createdAt':'2026-01-01T00:00:00Z'}]
    async def logs(self,did):return [{'timestamp':'2026-01-01T00:00:00Z','severity':'info','message':'bot ready'}]
    monkeypatch.setattr(RailwayClient,'deployments',deployments);monkeypatch.setattr(RailwayClient,'logs',logs)
    with TestClient(app) as client:
        client.cookies.set('blaze_session',sign_session(uid,'telegram'))
        response=client.get(f'/api/workloads/{wid}/logs');assert response.status_code==200 and response.json()['logs'][0]['message']=='bot ready'
        download=client.get(f'/api/workloads/{wid}/logs/download');assert download.status_code==200 and 'bot ready' in download.text
        metrics=client.get(f'/api/workloads/{wid}/metrics');data=metrics.json();assert data['allocation']=={'cpu_vcpus':1.0,'memory_mb':1024,'replicas':2};assert 'not live CPU/RAM' in data['note']
def test_console_frontend_uses_eventsource():
    source=open('static/blazenxt.js').read();assert 'new EventSource' in source and 'data-stream-url' not in source
    template=open('templates/server.html').read();assert 'data-stream-url' in template and 'data-health-chart' in template

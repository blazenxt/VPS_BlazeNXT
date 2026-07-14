import asyncio
from sqlalchemy import select
import app.services as service_module
from app.db import SessionLocal
from app.models import Artifact,State,User,Workload
from app.services import provision
class FakeRailway:
    fail_variables=True;created=0
    async def find_service(self,name):return {'id':'orphan-service','name':name}
    async def create_image_service(self,*args,**kwargs):self.__class__.created+=1;return 'new-service'
    async def upsert_variables(self,*args,**kwargs):
        if self.__class__.fail_variables:raise RuntimeError('variable mutation failed')
    async def update_limits(self,*args,**kwargs):return None
    async def update_instance(self,*args,**kwargs):return None
    async def redeploy(self,*args,**kwargs):return None
def seed(base):
    with SessionLocal() as db:
        user=User(telegram_id=-base,display_name='Recovery User');db.add(user);db.flush();artifact=Artifact(owner_id=user.id,filename='main.py',content_type='text/x-python',sha256='x'*64,size=8,data=b'print(1)');db.add(artifact);db.flush();workload=Workload(user_id=user.id,artifact_id=artifact.id,name='Recovery Bot',runtime='python',entrypoint='main.py');db.add(workload);db.commit();return workload.id
def test_provision_saves_reconciled_service_id_before_later_failure(monkeypatch):
    monkeypatch.setattr(service_module,'RailwayClient',FakeRailway);wid=seed(990001)
    with SessionLocal() as db:asyncio.run(provision(db,db.get(Workload,wid)))
    with SessionLocal() as db:
        workload=db.get(Workload,wid);assert workload.railway_service_id=='orphan-service';assert workload.state==State.failed;assert workload.last_error.startswith('environment synchronization:')
def test_retry_reuses_saved_service_and_recovers(monkeypatch):
    monkeypatch.setattr(service_module,'RailwayClient',FakeRailway);FakeRailway.fail_variables=False;wid=seed(990002)
    with SessionLocal() as db:
        workload=db.get(Workload,wid);workload.railway_service_id='saved-service';db.commit();asyncio.run(provision(db,workload))
    with SessionLocal() as db:
        workload=db.get(Workload,wid);assert workload.state==State.running;assert workload.railway_service_id=='saved-service';assert workload.last_error is None
    assert FakeRailway.created==0

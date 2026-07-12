from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.security import HTTPAuthorizationCredentials,HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import ApiKey,Role,State,User,Workload
from app.security import hash_token
from app.services import audit,perform_action
from app.webhooks import dispatch_event
router=APIRouter(prefix='/api/v1',tags=['API v1']);bearer=HTTPBearer(auto_error=False)
def api_identity(credentials:HTTPAuthorizationCredentials=Depends(bearer),db:Session=Depends(get_db)):
    if not credentials or credentials.scheme.lower()!='bearer':raise HTTPException(401,'Bearer API key required')
    key=db.scalar(select(ApiKey).where(ApiKey.key_hash==hash_token(credentials.credentials),ApiKey.revoked==False))
    if not key:raise HTTPException(401,'Invalid API key')
    user=db.get(User,key.user_id)
    if not user or user.banned:raise HTTPException(403,'Account unavailable')
    key.last_used_at=datetime.now(timezone.utc);db.commit();return user,key
def require(key,scope):
    import json
    scopes=json.loads(key.scopes)
    if scope not in scopes and '*' not in scopes:raise HTTPException(403,f'Missing scope: {scope}')
@router.get('/servers')
def servers(identity=Depends(api_identity),db:Session=Depends(get_db)):
    user,key=identity;require(key,'servers:read');rows=db.scalars(select(Workload).where(Workload.user_id==user.id,Workload.state!=State.deleted).order_by(Workload.created_at.desc())).all()
    return {'data':[{'id':w.id,'name':w.name,'state':w.state.value,'runtime':w.runtime,'entrypoint':w.entrypoint,'created_at':w.created_at} for w in rows]}
@router.get('/servers/{wid}')
def server(wid:int,identity=Depends(api_identity),db:Session=Depends(get_db)):
    user,key=identity;require(key,'servers:read');w=db.get(Workload,wid)
    if not w or (w.user_id!=user.id and user.role not in {Role.admin,Role.owner}):raise HTTPException(404)
    return {'data':{'id':w.id,'name':w.name,'state':w.state.value,'runtime':w.runtime,'entrypoint':w.entrypoint,'service_id':w.railway_service_id,'last_error':w.last_error}}
@router.post('/servers/{wid}/power/{action}')
async def power(wid:int,action:str,request:Request,identity=Depends(api_identity),db:Session=Depends(get_db)):
    user,key=identity;require(key,'servers:control');w=db.get(Workload,wid)
    if not w or (w.user_id!=user.id and user.role not in {Role.admin,Role.owner}):raise HTTPException(404)
    if action not in {'start','stop','restart'}:raise HTTPException(400,'Invalid power action')
    await perform_action(db,w,action);audit(db,user,f'workload.{action}.api',f'workload:{wid}',request.client.host if request.client else 'api',{'api_key':key.prefix});db.commit();await dispatch_event(wid,f'workload.{action}',{'source':'api'});return {'data':{'id':w.id,'state':w.state.value}}

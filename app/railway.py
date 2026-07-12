import httpx
from app.config import get_settings
class RailwayError(RuntimeError):pass
class RailwayClient:
    def __init__(self):self.s=get_settings()
    @property
    def configured(self):return all((self.s.railway_api_token,self.s.railway_project_id,self.s.railway_environment_id))
    async def gql(self,q,v):
        if not self.configured:raise RailwayError('Railway provider is not configured')
        async with httpx.AsyncClient(timeout=30) as c:r=await c.post(self.s.railway_api_url,headers={'Authorization':f'Bearer {self.s.railway_api_token}'},json={'query':q,'variables':v})
        r.raise_for_status(); body=r.json()
        if body.get('errors'):raise RailwayError(body['errors'][0].get('message','Railway API error'))
        return body['data']
    async def create_image_service(self,name,image,variables=None):
        q='mutation($input:ServiceCreateInput!){serviceCreate(input:$input){id name}}';d=await self.gql(q,{'input':{'projectId':self.s.railway_project_id,'name':name,'source':{'image':image}}});sid=d['serviceCreate']['id']
        if variables:await self.upsert_variables(sid,variables)
        return sid
    async def create(self,name,variables):return await self.create_image_service(name,self.s.railway_runner_image,variables)
    async def create_volume(self,sid,mount_path):
        q='mutation($input:VolumeCreateInput!){volumeCreate(input:$input){id}}';d=await self.gql(q,{'input':{'projectId':self.s.railway_project_id,'serviceId':sid,'mountPath':mount_path}});return d['volumeCreate']['id']
    async def update_limits(self,sid,cpu_vcpus,memory_mb):
        q='mutation($input:ServiceInstanceLimitsUpdateInput!){serviceInstanceLimitsUpdate(input:$input)}';return await self.gql(q,{'input':{'serviceId':sid,'environmentId':self.s.railway_environment_id,'vCPUs':float(cpu_vcpus),'memoryGB':float(memory_mb)/1024}})
    async def update_instance(self,sid,replicas=1,restart_policy='ON_FAILURE',restart_retries=5):
        q='mutation($s:String!,$e:String!,$input:ServiceInstanceUpdateInput!){serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$input)}';return await self.gql(q,{'s':sid,'e':self.s.railway_environment_id,'input':{'numReplicas':replicas,'restartPolicyType':restart_policy,'restartPolicyMaxRetries':restart_retries}})
    async def upsert_variables(self,sid,variables):
        q='mutation($input:VariableCollectionUpsertInput!){variableCollectionUpsert(input:$input)}'
        return await self.gql(q,{'input':{'projectId':self.s.railway_project_id,'environmentId':self.s.railway_environment_id,'serviceId':sid,'variables':variables}})
    async def redeploy(self,sid):
        return await self.gql('mutation($s:String!,$e:String!){serviceInstanceRedeploy(serviceId:$s,environmentId:$e)}',{'s':sid,'e':self.s.railway_environment_id})
    async def delete(self,sid):return await self.gql('mutation($id:String!){serviceDelete(id:$id)}',{'id':sid})
    async def rollback(self,deployment_id):return await self.gql('mutation($id:String!){deploymentRollback(id:$id){id}}',{'id':deployment_id})
    async def deployments(self,sid):
        q='query($input:DeploymentListInput!){deployments(first:10,input:$input){edges{node{id status createdAt}}}}'
        d=await self.gql(q,{'input':{'projectId':self.s.railway_project_id,'environmentId':self.s.railway_environment_id,'serviceId':sid}});return [e['node'] for e in d['deployments']['edges']]
    async def stop(self,deployment_id):return await self.gql('mutation($id:String!){deploymentRemove(id:$id)}',{'id':deployment_id})
    async def logs(self,did):
        d=await self.gql('query($id:String!){deploymentLogs(deploymentId:$id,limit:300){message timestamp severity}}',{'id':did});return d['deploymentLogs']

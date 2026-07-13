import asyncio,hashlib,re,uuid
from pathlib import PurePosixPath
import boto3
from botocore.config import Config
from app.config import get_settings
s=get_settings()
class ObjectStorageError(RuntimeError):pass
class ObjectStorage:
    @property
    def configured(self):return bool(s.s3_bucket and s.s3_access_key_id and s.s3_secret_access_key)
    def client(self):
        if not self.configured:raise ObjectStorageError('S3-compatible object storage is not configured')
        kwargs={'service_name':'s3','aws_access_key_id':s.s3_access_key_id,'aws_secret_access_key':s.s3_secret_access_key,'region_name':s.s3_region,'config':Config(signature_version='s3v4',s3={'addressing_style':'path' if s.s3_force_path_style else 'virtual'},retries={'max_attempts':3,'mode':'standard'})}
        if s.s3_endpoint_url:kwargs['endpoint_url']=s.s3_endpoint_url
        return boto3.client(**kwargs)
    def key(self,workload_id,filename):
        safe=re.sub(r'[^A-Za-z0-9_.-]','_',PurePosixPath(filename).name)[:120] or 'artifact.bin';prefix=s.s3_prefix.strip('/');return f'{prefix}/workloads/{int(workload_id)}/{uuid.uuid4().hex}/{safe}'
    async def upload(self,workload_id,filename,data):
        if len(data)>s.offsite_backup_max_mb*1024*1024:raise ObjectStorageError(f'Backup exceeds {s.offsite_backup_max_mb} MB offsite limit')
        key=self.key(workload_id,filename);sha=hashlib.sha256(data).hexdigest();client=self.client()
        await asyncio.to_thread(client.put_object,Bucket=s.s3_bucket,Key=key,Body=data,ContentType='application/octet-stream',Metadata={'sha256':sha,'workload-id':str(workload_id)})
        return {'bucket':s.s3_bucket,'key':key,'sha256':sha,'size':len(data)}
    async def download(self,key):
        client=self.client();response=await asyncio.to_thread(client.get_object,Bucket=s.s3_bucket,Key=key);return await asyncio.to_thread(response['Body'].read)
    async def head(self,key):
        client=self.client();return await asyncio.to_thread(client.head_object,Bucket=s.s3_bucket,Key=key)
    async def delete(self,key):
        client=self.client();await asyncio.to_thread(client.delete_object,Bucket=s.s3_bucket,Key=key)
    async def check(self):
        client=self.client();await asyncio.to_thread(client.head_bucket,Bucket=s.s3_bucket);return True
storage=ObjectStorage()

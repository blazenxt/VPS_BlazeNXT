import json,logging,sys
from datetime import datetime,timezone
from app.config import get_settings
s=get_settings()
class JsonFormatter(logging.Formatter):
    def format(self,record):
        payload={'timestamp':datetime.now(timezone.utc).isoformat(),'level':record.levelname.lower(),'logger':record.name,'message':record.getMessage()}
        for key in ('request_id','method','path','status','duration_ms','client_ip','workload_id','user_id'):
            value=getattr(record,key,None)
            if value is not None:payload[key]=value
        if record.exc_info:payload['exception']=self.formatException(record.exc_info)
        return json.dumps(payload,separators=(',',':'),ensure_ascii=False)
def configure_logging():
    handler=logging.StreamHandler(sys.stdout);handler.setFormatter(JsonFormatter() if s.json_logs else logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    root=logging.getLogger();root.handlers=[handler];root.setLevel(logging.INFO)
    for name in ('uvicorn','uvicorn.error','alembic'):logger=logging.getLogger(name);logger.handlers=[];logger.propagate=True

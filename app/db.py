from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import get_settings
s=get_settings(); kwargs={'pool_pre_ping':True}; database_url=s.database_url
# Railway exposes postgres:// or postgresql://, while this image uses psycopg v3.
if database_url.startswith('postgres://'):
    database_url='postgresql+psycopg://'+database_url.removeprefix('postgres://')
elif database_url.startswith('postgresql://'):
    database_url='postgresql+psycopg://'+database_url.removeprefix('postgresql://')
if database_url.startswith('sqlite'): kwargs['connect_args']={'check_same_thread':False}
engine=create_engine(database_url,**kwargs)
SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
def get_db():
    db=SessionLocal()
    try: yield db
    finally: db.close()

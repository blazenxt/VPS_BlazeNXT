from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import get_settings
s=get_settings(); kwargs={'pool_pre_ping':True}
if s.database_url.startswith('sqlite'): kwargs['connect_args']={'check_same_thread':False}
engine=create_engine(s.database_url,**kwargs)
SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
def get_db():
    db=SessionLocal()
    try: yield db
    finally: db.close()

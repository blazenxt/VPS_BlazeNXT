from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect,text
from app.config import get_settings
from app.db import Base,engine
import app.models  # noqa: F401
s=get_settings();MIGRATION_STATUS={'state':'not_started','revision':None,'error':None,'bootstrap':None}
def alembic_config():
    root=Path(__file__).resolve().parent.parent;cfg=Config(str(root/'alembic.ini'));cfg.set_main_option('script_location',str(root/'migrations'));return cfg
def current_revision():
    if 'alembic_version' not in inspect(engine).get_table_names():return None
    with engine.connect() as connection:return connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
def run_migrations():
    if not s.migrations_enabled:
        Base.metadata.create_all(engine);MIGRATION_STATUS.update({'state':'disabled','revision':current_revision(),'error':None,'bootstrap':'create_all'});return MIGRATION_STATUS
    MIGRATION_STATUS.update({'state':'running','error':None});lock=None
    try:
        if engine.dialect.name=='postgresql':
            lock=engine.connect();lock.execute(text('SELECT pg_advisory_lock(744219001)'))
        tables=set(inspect(engine).get_table_names());cfg=alembic_config()
        if 'alembic_version' not in tables and 'users' in tables:
            # Existing BlazeNXT installations predate Alembic. Add any missing
            # current tables, then stamp the baseline without dropping data.
            Base.metadata.create_all(engine);command.stamp(cfg,'head');MIGRATION_STATUS['bootstrap']='existing_schema_stamped'
        else:
            command.upgrade(cfg,'head');MIGRATION_STATUS['bootstrap']='upgraded'
        revision=current_revision();MIGRATION_STATUS.update({'state':'ready','revision':revision,'error':None});return MIGRATION_STATUS
    except Exception as exc:
        MIGRATION_STATUS.update({'state':'failed','error':str(exc)[:1000]});raise
    finally:
        if lock:
            try:lock.execute(text('SELECT pg_advisory_unlock(744219001)'))
            finally:lock.close()

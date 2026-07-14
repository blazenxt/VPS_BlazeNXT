from sqlalchemy import inspect,text
from fastapi.testclient import TestClient
from app.db import engine
from app.main import MIGRATION_STATUS,app
from app.migrations import run_migrations

def test_migration_bootstrap_is_idempotent():
    first=run_migrations();second=run_migrations()
    assert first['state']=='ready' and second['state']=='ready'
    assert first['revision']=='0001_blazenxt_v1'
    assert 'alembic_version' in inspect(engine).get_table_names()
    with engine.connect() as connection:assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0001_blazenxt_v1'
def test_request_id_and_error_formats():
    with TestClient(app) as client:
        html=client.get('/this-page-does-not-exist',headers={'X-Request-ID':'quality-test-id'})
        assert html.status_code==404 and html.headers['x-request-id']=='quality-test-id' and 'Request ID: quality-test-id' in html.text
        api=client.get('/api/v1/does-not-exist',headers={'Accept':'application/json'})
        assert api.status_code==404 and api.json()['request_id']==api.headers['x-request-id']
def test_readiness_reports_migration_revision():
    with TestClient(app) as client:
        data=client.get('/health/ready').json()
        assert data['migration_state']=='ready' and data['database_revision']=='0001_blazenxt_v1'
def test_large_static_asset_can_be_compressed():
    with TestClient(app) as client:
        response=client.get('/static/blazenxt.css',headers={'Accept-Encoding':'gzip'})
        assert response.status_code==200 and response.headers.get('content-encoding')=='gzip'

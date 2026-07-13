import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app

def test_manifest_has_installable_assets():
    manifest=json.loads(Path('static/manifest.webmanifest').read_text())
    assert manifest['display']=='standalone' and manifest['start_url'].startswith('/dashboard')
    assert {icon['sizes'] for icon in manifest['icons']}=={'192x192','512x512'}
    for icon in manifest['icons']:assert Path(icon['src'].lstrip('/')).exists()
def test_service_worker_avoids_private_route_caching():
    worker=Path('static/service-worker.js').read_text()
    assert "url.pathname.startsWith('/api/')" in worker
    assert "request.mode==='navigate'" in worker
    assert "caches.match('/static/offline.html')" in worker
def test_service_worker_scope_header():
    with TestClient(app) as client:
        response=client.get('/service-worker.js')
        assert response.status_code==200
        assert response.headers['service-worker-allowed']=='/'
        assert response.headers['cache-control'].startswith('no-cache')

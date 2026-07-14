from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import ERROR_PAGES,app

@app.get('/__tests__/error/{code}')
def trigger_error(code:int):
    if code==500:raise RuntimeError('test-only failure')
    raise HTTPException(code,f'test detail for {code}')
@app.get('/__tests__/validation')
def trigger_validation(required:int):return required

def test_every_supported_error_has_branded_frontend():
    codes=[400,401,403,404,409,413,429,500,502,503]
    assert set(codes).issubset(ERROR_PAGES)
    with TestClient(app,raise_server_exceptions=False) as client:
        for code in codes:
            request_id=f'error-{code}';response=client.get(f'/__tests__/error/{code}',headers={'X-Request-ID':request_id})
            assert response.status_code==code
            assert f'>{code}<' in response.text
            assert ERROR_PAGES[code]['title'] in response.text
            assert request_id in response.text and 'data-copy-text' in response.text
            assert f"tone-{ERROR_PAGES[code]['tone']}" in response.text
        assert 'data-reload' in client.get('/__tests__/error/429').text
def test_validation_error_has_422_frontend():
    with TestClient(app) as client:
        response=client.get('/__tests__/validation',headers={'X-Request-ID':'validation-id'})
        assert response.status_code==422 and 'Validation failed' in response.text and 'validation-id' in response.text
def test_api_errors_stay_json():
    with TestClient(app) as client:
        response=client.get('/api/v1/not-real',headers={'X-Request-ID':'api-error-id','Accept':'application/json'})
        assert response.status_code==404 and response.json()['request_id']=='api-error-id'

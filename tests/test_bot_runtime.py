from fastapi.testclient import TestClient
from app.main import TELEGRAM_HEADER_SECRET,app,s

def test_telegram_webhook_requires_secret_header():
    with TestClient(app) as client:
        response=client.post(f'/telegram/webhook/{s.telegram_webhook_secret}',json={'update_id':99110001})
        assert response.status_code==404
def test_telegram_webhook_deduplicates_updates():
    headers={'X-Telegram-Bot-Api-Secret-Token':TELEGRAM_HEADER_SECRET}
    with TestClient(app) as client:
        first=client.post(f'/telegram/webhook/{s.telegram_webhook_secret}',headers=headers,json={'update_id':99110002})
        second=client.post(f'/telegram/webhook/{s.telegram_webhook_secret}',headers=headers,json={'update_id':99110002})
        assert first.status_code==200
        assert second.json().get('duplicate') is True

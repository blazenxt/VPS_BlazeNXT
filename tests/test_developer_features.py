import pyotp,pytest
from app.catalog import PRESETS
from app.webhooks import validate_webhook_url

def test_presets_have_supported_runtime_and_safe_entrypoint():
    assert PRESETS
    for preset in PRESETS.values():
        assert preset['runtime'] in {'python','node'}
        assert '/' not in preset['entrypoint'] and '..' not in preset['entrypoint']
def test_totp_roundtrip():
    secret=pyotp.random_base32();totp=pyotp.TOTP(secret)
    assert totp.verify(totp.now())
def test_webhooks_block_private_networks():
    with pytest.raises(ValueError):validate_webhook_url('https://127.0.0.1/hook')
    with pytest.raises(ValueError):validate_webhook_url('http://example.com/hook')

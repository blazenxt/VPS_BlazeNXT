from app.auth import synthetic_telegram_id
from app.security import read_magic_link,read_oauth_state,sign_magic_link,sign_oauth_state

def test_oauth_state_is_provider_bound():
    state=sign_oauth_state('google',42,'csrf-value')
    assert read_oauth_state(state,'google')['uid']==42
    assert read_oauth_state(state,'github') is None
def test_magic_link_payload_is_signed():
    token=sign_magic_link('User@Example.com',7,'csrf')
    payload=read_magic_link(token)
    assert payload['email']=='user@example.com' and payload['uid']==7
def test_synthetic_ids_are_stable_and_negative():
    assert synthetic_telegram_id('google','abc')==synthetic_telegram_id('google','abc')<0
    assert synthetic_telegram_id('github','abc')!=synthetic_telegram_id('google','abc')

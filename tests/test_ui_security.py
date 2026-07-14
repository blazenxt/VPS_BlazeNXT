from app.main import safe_frame_origins

def test_frame_origin_allowlist_accepts_exact_https_origins():
    assert safe_frame_origins('https://portal.example.com, https://tools.example.com/')==['https://portal.example.com','https://tools.example.com']
def test_frame_origin_allowlist_rejects_unsafe_values():
    assert safe_frame_origins('http://insecure.example,*,https://user:pass@example.com/path')==[]
def test_frame_ancestors_none_wins():
    assert safe_frame_origins("https://portal.example.com,'none'",True)==["'none'"]
def test_compact_footer_and_announcements_are_present():
    base=open('templates/base.html').read();home=open('templates/home.html').read()
    assert 'site-footer' in base and 'app-footer' not in base
    assert 'Hosting Control v1' in base and 'public-announcements' in home

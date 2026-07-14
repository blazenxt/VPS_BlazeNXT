from app.main import safe_frame_origins

def test_frame_origin_allowlist_accepts_exact_https_origins():
    assert safe_frame_origins('https://portal.example.com, https://tools.example.com/')==['https://portal.example.com','https://tools.example.com']
def test_frame_origin_allowlist_rejects_unsafe_values():
    assert safe_frame_origins('http://insecure.example,*,https://user:pass@example.com/path')==[]
def test_frame_ancestors_none_wins():
    assert safe_frame_origins("https://portal.example.com,'none'",True)==["'none'"]
def test_footer_and_landing_announcement_clutter_are_absent():
    assert 'app-footer' not in open('templates/base.html').read()
    assert 'public-announcements' not in open('templates/home.html').read()

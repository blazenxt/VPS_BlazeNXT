from app.main import safe_frame_origins

def test_frame_origin_allowlist_accepts_exact_https_origins():
    assert safe_frame_origins('https://portal.example.com, https://tools.example.com/')==['https://portal.example.com','https://tools.example.com']
def test_frame_origin_allowlist_rejects_unsafe_values():
    assert safe_frame_origins('http://insecure.example,*,https://user:pass@example.com/path')==[]
def test_frame_ancestors_none_wins():
    assert safe_frame_origins("https://portal.example.com,'none'",True)==["'none'"]
def test_global_footer_is_present():
    template=open('templates/base.html').read()
    assert 'app-footer' in template and 'BlazeNXT Hosting' in template and 'powered by Railway' in template

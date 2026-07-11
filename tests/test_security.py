import io,zipfile,pytest
from app.security import decrypt_secret,encrypt_secret,inspect_zip,safe_filename
def makezip(name='main.py'):
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w') as z:z.writestr(name,b"print('ok')")
 return out.getvalue()
def test_name():assert safe_filename('main.py')=='main.py'
def test_zip():inspect_zip(makezip())
def test_secret_roundtrip():
 value='telegram-token-example';encrypted=encrypt_secret(value)
 assert encrypted!=value and decrypt_secret(encrypted)==value
def test_traversal():
 with pytest.raises(ValueError):inspect_zip(makezip('../escape.py'))

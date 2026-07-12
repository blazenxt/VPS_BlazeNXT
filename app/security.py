import base64,hashlib,hmac,io,re,secrets,time,zipfile
from pathlib import PurePosixPath
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature,SignatureExpired,URLSafeTimedSerializer
from app.config import get_settings
s=get_settings(); signer=URLSafeTimedSerializer(s.app_secret,salt='blaze-session-v1');oauth_signer=URLSafeTimedSerializer(s.app_secret,salt='blaze-oauth-state-v1');magic_signer=URLSafeTimedSerializer(s.app_secret,salt='blaze-magic-link-v1');preauth_signer=URLSafeTimedSerializer(s.app_secret,salt='blaze-preauth-v1'); SAFE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
def sign_session(uid): return signer.dumps({'uid':uid,'csrf':secrets.token_urlsafe(24)})
def read_session(v):
    try:return signer.loads(v,max_age=s.session_ttl_seconds)
    except (BadSignature,SignatureExpired):return None
def sign_oauth_state(provider,link_uid=None,bind=None):return oauth_signer.dumps({'provider':provider,'uid':link_uid,'bind':bind,'nonce':secrets.token_urlsafe(16)})
def read_oauth_state(value,provider):
    try:data=oauth_signer.loads(value,max_age=600)
    except (BadSignature,SignatureExpired):return None
    return data if data.get('provider')==provider else None
def sign_magic_link(email,link_uid=None,bind=None):return magic_signer.dumps({'email':email.lower(),'uid':link_uid,'bind':bind,'nonce':secrets.token_urlsafe(16)})
def read_magic_link(value):
    try:return magic_signer.loads(value,max_age=s.magic_link_ttl_seconds)
    except (BadSignature,SignatureExpired):return None
def sign_preauth(uid):return preauth_signer.dumps({'uid':uid,'nonce':secrets.token_urlsafe(16)})
def read_preauth(value):
    try:return preauth_signer.loads(value,max_age=300)
    except (BadSignature,SignatureExpired):return None
def verify_telegram(data):
    supplied=data.get('hash',''); clean={k:str(v) for k,v in data.items() if k!='hash' and v is not None}
    try:
        if abs(int(time.time())-int(clean.get('auth_date','0')))>300:return False
    except ValueError:return False
    check='\n'.join(f'{k}={clean[k]}' for k in sorted(clean)); key=hashlib.sha256(s.bot_token.encode()).digest()
    return hmac.compare_digest(hmac.new(key,check.encode(),hashlib.sha256).hexdigest(),supplied)
def hash_token(v):return hmac.new(s.app_secret.encode(),v.encode(),hashlib.sha256).hexdigest()
def _fernet():return Fernet(base64.urlsafe_b64encode(hashlib.sha256(s.app_secret.encode()).digest()))
def encrypt_secret(value):return _fernet().encrypt(value.encode()).decode()
def decrypt_secret(value):
    try:return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:raise ValueError('Unable to decrypt stored secret')
def safe_filename(name):
    if '/' in name or '\\' in name: raise ValueError('paths are not allowed')
    if not SAFE.fullmatch(name) or name.startswith('.'):raise ValueError('unsafe filename')
    return name
def inspect_zip(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos=z.infolist()
        if len(infos)>300:raise ValueError('archive has too many files')
        total=0
        for i in infos:
            p=PurePosixPath(i.filename.replace('\\','/'))
            if p.is_absolute() or '..' in p.parts:raise ValueError('unsafe archive path')
            if i.is_dir():continue
            total+=i.file_size
            if i.file_size>25*1024*1024 or total>50*1024*1024:raise ValueError('archive expands beyond limit')
            if i.compress_size and i.file_size/i.compress_size>100:raise ValueError('suspicious compression ratio')

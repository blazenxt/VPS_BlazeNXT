import re,time
from sqlalchemy import select
from app.db import SessionLocal
from app.models import BrandAsset,PlatformSetting
DEFAULTS={'name':'BlazeNXT','tagline':'HOSTING CONTROL · v1','landing_kicker':'Isolated hosting on Railway','landing_title':'Ship your bot.','landing_accent':'Control everything.','landing_subtitle':'One focused panel for deployment, files, environment secrets, logs, backups, resources and Telegram controls.','footer_text':'Secure bot hosting and infrastructure control on Railway.','primary_color':'#ff6a3d','accent_color':'#22c8e5'}
_cache={'at':0.0,'value':None}
def invalidate_brand():_cache.update({'at':0.0,'value':None})
def get_brand(force=False):
    if not force and _cache['value'] and time.monotonic()-_cache['at']<60:return _cache['value']
    with SessionLocal() as db:
        rows=db.scalars(select(PlatformSetting).where(PlatformSetting.key.like('brand.%'))).all();values=DEFAULTS.copy();values.update({row.key.removeprefix('brand.'):row.value for row in rows if row.key.removeprefix('brand.') in DEFAULTS});asset=db.scalar(select(BrandAsset).order_by(BrandAsset.updated_at.desc()).limit(1));values['logo_url']='/brand/logo?v='+str(int(asset.updated_at.timestamp())) if asset else '/static/blazenxt-logo.png?v=2'
    for key in ('primary_color','accent_color'):
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',values[key]):values[key]=DEFAULTS[key]
    _cache.update({'at':time.monotonic(),'value':values});return values

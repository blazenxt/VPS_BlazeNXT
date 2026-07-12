import io,zipfile
import pytest
from fastapi import HTTPException
from app.main import read_artifact_text,rebuild_artifact
from app.models import Artifact

def artifact():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('main.py',b"print('old')")
    return Artifact(owner_id=1,filename='bot.zip',content_type='application/zip',sha256='x'*64,size=len(out.getvalue()),data=out.getvalue())
def test_edit_zip_file():
    a=artifact();data=rebuild_artifact(a,'main.py',b"print('new')")
    updated=Artifact(owner_id=1,filename='bot.zip',content_type='application/zip',sha256='y'*64,size=len(data),data=data)
    assert read_artifact_text(updated,'main.py')=="print('new')"
def test_create_and_delete_zip_file():
    a=artifact();data=rebuild_artifact(a,'config.json',b'{}');a.data=data;a.size=len(data)
    assert read_artifact_text(a,'config.json')=='{}'
    data=rebuild_artifact(a,'config.json',None,delete=True)
    a.data=data
    with pytest.raises(HTTPException):read_artifact_text(a,'config.json')
def test_archive_traversal_rejected():
    with pytest.raises(HTTPException):rebuild_artifact(artifact(),'../escape.py',b'x')

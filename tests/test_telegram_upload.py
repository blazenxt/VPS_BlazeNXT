import io,zipfile
import pytest
from app.models import Artifact,TelegramUploadDraft
from app.telegram_bot import draft_keyboard,infer_upload_config

def archive(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    return out.getvalue()
def test_infers_python_zip_entrypoint():
    assert infer_upload_config('bot.zip',archive({'requirements.txt':'aiogram','main.py':'print(1)'}))==('python','main.py')
def test_infers_node_zip_entrypoint():
    assert infer_upload_config('bot.zip',archive({'package.json':'{}','index.js':'console.log(1)'}))==('node','index.js')
def test_rejects_zip_without_root_entrypoint():
    with pytest.raises(ValueError):infer_upload_config('bot.zip',archive({'src/worker.py':'print(1)'}))
def test_single_script_runtime_buttons_are_locked():
    artifact=Artifact(owner_id=1,filename='main.py',content_type='text/x-python',sha256='x'*64,size=1,data=b'x');draft=TelegramUploadDraft(id=7,user_id=1,artifact_id=1,name='Bot',runtime='python',entrypoint='main.py');draft.artifact=artifact
    buttons=draft_keyboard(draft)
    assert len(buttons[0])==1 and buttons[0][0]['callback_data']=='upl:python:7'
    assert buttons[1][0]['callback_data']=='upl:deploy:7'

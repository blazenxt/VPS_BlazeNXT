#!/usr/bin/env python3
import hashlib,os,pathlib,subprocess,sys,urllib.request,zipfile
base=os.environ['CONTROL_PLANE_URL'].rstrip('/');wid=os.environ['WORKLOAD_ID'];token=os.environ['RUNNER_TOKEN']
req=urllib.request.Request(f'{base}/internal/artifacts/{wid}',headers={'Authorization':f'Bearer {token}'})
with urllib.request.urlopen(req,timeout=60) as r:data=r.read();name=r.headers.get('X-Filename','upload');expected=r.headers.get('X-Content-SHA256','')
if not expected or hashlib.sha256(data).hexdigest()!=expected:raise SystemExit('artifact integrity check failed')
root=pathlib.Path('/workspace/app');root.mkdir(parents=True,exist_ok=True)
if name.endswith('.zip'):
    archive=pathlib.Path('/tmp/artifact.zip');archive.write_bytes(data)
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            target=(root/member.filename).resolve()
            if root.resolve() not in target.parents and target!=root.resolve():raise SystemExit('unsafe archive')
        z.extractall(root)
else:(root/name).write_bytes(data)
os.chdir(root);runtime=os.getenv('RUNTIME','python');entry=os.getenv('ENTRYPOINT','main.py')
if not pathlib.Path(entry).is_file():raise SystemExit(f'entrypoint not found: {entry}')
if runtime=='python' and pathlib.Path('requirements.txt').is_file():subprocess.run([sys.executable,'-m','pip','install','--user','-r','requirements.txt'],check=True,timeout=300)
elif runtime=='node' and pathlib.Path('package.json').is_file():subprocess.run(['npm','install','--omit=dev','--ignore-scripts'],check=True,timeout=300)
cmd=[sys.executable,'-I',entry] if runtime=='python' else ['node','--disable-proto=delete',entry];os.execvp(cmd[0],cmd)

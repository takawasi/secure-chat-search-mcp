"""ビルド済みwheelを新しいvenvへ導入して確認。依存ライブラリは元環境を参照する。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def run(command,**kwargs):
    result=subprocess.run(command,text=True,capture_output=True,**kwargs)
    if result.returncode:
        print(result.stdout);print(result.stderr,file=sys.stderr)
        raise RuntimeError('インストール検証に失敗しました')
    return result

def main():
    wheel=next((ROOT/'dist').glob('*.whl'))
    with tempfile.TemporaryDirectory() as tmp:
        v=Path(tmp)/'venv'
        run([sys.executable,'-m','venv',str(v)])
        windows=os.name=='nt'
        py=str(v/('Scripts/python.exe' if windows else 'bin/python'))
        cli=str(v/('Scripts/chat-search.exe' if windows else 'bin/chat-search'))
        run([py,'-m','pip','install','--no-index','--no-deps',str(wheel)])
        # オフライン環境用。元環境のライブラリを参照し、プロジェクトsrcは渡さない。
        dependency_paths=[p for p in sys.path if p and not Path(p).resolve().is_relative_to(ROOT)]
        env={**os.environ,'PYTHONPATH':os.pathsep.join(dependency_paths)}
        script='''import json,secure_chat_search
from fastapi.testclient import TestClient
from secure_chat_search.app import create_app
from secure_chat_search.config import Settings
app=create_app(Settings(database_url="sqlite:///:memory:"))
with TestClient(app) as c:
 token=c.post("/api/demo/session",json={"subject":"aoki"}).json()["access_token"]
 r=c.post("/api/search",headers={"Authorization":"Bearer "+token},json={"query":"納期"})
 assert r.status_code==200 and r.json()["results"]
 assert c.get("/static/app.js").status_code==200
 print(json.dumps({"installed_import":True,"site_packages":"site-packages" in secure_chat_search.__file__,"search":True,"packaged_ui":True}))
'''
        result=json.loads(run([py,'-c',script],cwd=tmp,env=env).stdout.strip())
        assert result['site_packages']
        env['SCS_DATABASE_URL']='sqlite:///'+str(Path(tmp)/'installed.db')
        run([cli,'init-db'],cwd=tmp,env=env)
        result.update({'console_entry':True,'mode':'ローカルwheelを新しいvenvへ導入。依存ライブラリのみ元環境から参照し、プロジェクトsrcは参照しない。オンラインでの新規依存解決ではない。','wheel':wheel.name})
        (ROOT/'reports/install-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

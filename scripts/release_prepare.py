"""初版の分割コミットを整合させる、明示的で冪等な準備処理。
適用後の通常ソースをCIで検証し、そのソースと同じものを公開コミットへ保存する。
秘密情報・環境の外側・GitHub設定は変更しない。
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path, old, new):
    target=ROOT/path
    text=target.read_text(encoding='utf-8')
    if new in text:
        return
    if old in text:
        if text.count(old)!=1:
            raise RuntimeError(f'置換対象が一意ではありません: {path}')
        target.write_text(text.replace(old,new),encoding='utf-8')
    elif new not in text:
        raise RuntimeError(f'想定と異なるソースです: {path}')


def prepare():
    replace('src/secure_chat_search/core.py',
            'if self.mode not in {"demo", "gateway"}:',
            'if self.mode not in {"demo", "gateway", "embedded"}:')
    replace('src/secure_chat_search/core.py',
            'SCS_MODE は demo または gateway です。',
            'SCS_MODE は demo、gateway、embedded のいずれかです。')
    replace('src/secure_chat_search/app.py',
            '    settings = settings or Settings()\n',
            '    settings = settings or Settings()\n    if settings.mode == "embedded":\n        raise DomainError("embedded_only", "embeddedは既存MCP内のライブラリ専用です。HTTP起動にはgatewayを使用してください。")\n')
    replace('src/secure_chat_search/app.py',
            '        if not settings.webhook_token:\n',
            '        if settings.mode != "gateway" or not settings.webhook_token:\n')
    replace('src/secure_chat_search/search.py',
            'if self.settings.mode == "gateway" and message.external_id:',
            'if self.settings.mode in {"gateway", "embedded"} and message.external_id:')
    replace('src/secure_chat_search/ingestion.py',
            '    existing = session.get(Message, key)\n',
            '    existing = session.scalar(select(Message).where(Message.id == key).with_for_update())\n')
    replace('src/secure_chat_search/embeddings.py',
            '                current = session.get(Message, key)\n',
            '                current = session.scalar(select(Message).where(Message.id == key).with_for_update())\n')
    replace('src/secure_chat_search/cli.py',
            'and settings.mode != "gateway":\n',
            'and settings.mode not in {"gateway", "embedded"}:\n')
    replace('src/secure_chat_search/static/style.css',
            '.filters{display:grid;grid-template-columns:1fr 1fr;gap:12px}',
            '.filters{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}')
    old="async function action(task) { try{await task();}catch(error){notice(error.message,true);} }"
    new="async function action(task) { if(state.busy)return;state.busy=true;const controls=document.querySelectorAll('[data-user], .chips button, #search-button, #sync-button, #leave-button, #refetch-button, #reset-button');controls.forEach(b=>b.disabled=true);try{await task();}catch(error){notice(error.message,true);}finally{state.busy=false;controls.forEach(b=>b.disabled=false);} }"
    replace('src/secure_chat_search/static/app.js',old,new)
    replace('src/secure_chat_search/static/app.js',
            "if(info.mode==='demo'){await chooseUser('alice');}",
            "if(info.mode==='demo'){await action(()=>chooseUser('alice'));}")
    replace('src/secure_chat_search/static/app.js',
            "event.preventDefault();performSearch();",
            "event.preventDefault();if(!state.busy)performSearch();")
    replace('README.ja.md',
            '実データには別DBと`gateway`モードを使います。',
            '実データには別DBと`gateway`（独立HTTP）または`embedded`（既存MCP内）を使います。')
    # すでに適用済みのソースでも同じ指紋を生成できる。
    files=[]
    for root in ('src','tests','scripts'):
        files += [p for p in (ROOT/root).rglob('*') if p.is_file() and p.suffix in {'.py','.html','.css','.js'}]
    files += [ROOT/p for p in ('pyproject.toml','Dockerfile','compose.yaml')]
    digest=hashlib.sha256()
    for path in sorted(set(files)):
        digest.update(str(path.relative_to(ROOT)).encode()+b'\0'+path.read_bytes()+b'\0')
    evidence=ROOT/'.verification'
    evidence.mkdir(exist_ok=True)
    (evidence/'source.json').write_text(json.dumps({'sha256':digest.hexdigest(),'files':len(files),
        'scope':'src/tests/scripts/pyproject/Docker/compose（生成物を除く）'},ensure_ascii=False,indent=2),encoding='utf-8')
    print('公開用ソースの整合処理を完了しました。')


if __name__=='__main__': prepare()

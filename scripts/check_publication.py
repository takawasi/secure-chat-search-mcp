"""配布対象の秘密らしい文字列・相対リンク等を点検。完全なセキュリティ検証ではない。"""
import json
from pathlib import Path
import re
from urllib.parse import unquote,urlparse

ROOT=Path(__file__).resolve().parents[1]
IGNORED_DIRS={'.git','.venv','venv','__pycache__','.pytest_cache','build','dist','data','secrets','htmlcov'}
TEXT_SUFFIXES={'.py','.md','.txt','.html','.css','.js','.json','.yaml','.yml','.toml','.sh','.ps1','.csv','.example'}
PATTERNS={
 'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/]{40,}'),
 'github_token':re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
 'api_key':re.compile(r'\bsk-proj-[A-Za-z0-9_-]{30,}\b'),
}

def ignored(path):
    rel=path.relative_to(ROOT)
    return any(x in IGNORED_DIRS or x.endswith('.egg-info') for x in rel.parts) or path.name in {'.coverage','MANIFEST.sha256'} or path.suffix in {'.log','.pyc'}

def main():
    issues=[];scanned=0;ignored_count=0;links=0
    for path in sorted(ROOT.rglob('*')):
        if not path.is_file():continue
        if ignored(path):ignored_count+=1;continue
        rel=str(path.relative_to(ROOT))
        if path.is_symlink():issues.append({'path':rel,'type':'symlink'});continue
        if path.name=='.env' or path.suffix in {'.pem','.key','.db','.sqlite','.sqlite3'}:
            issues.append({'path':rel,'type':'forbidden_file'});continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in {'Dockerfile','.gitignore','.dockerignore'}:continue
        text=path.read_text(encoding='utf-8');scanned+=1
        for name,pattern in PATTERNS.items():
            if pattern.search(text):issues.append({'path':rel,'type':name})
        if path.suffix=='.md':
            for target in re.findall(r'!?\[[^\]\n]+\]\(([^)\n]+)\)',text):
                target=target.split(' "',1)[0]
                if urlparse(target).scheme or target.startswith('#'):continue
                links+=1
                dest=(path.parent/unquote(target.split('#',1)[0])).resolve()
                if not dest.exists():issues.append({'path':rel,'type':'missing_link','target':target})
    result={'scanned_text_files':scanned,'checked_relative_links':links,'excluded_runtime_files':ignored_count,'issues':issues,'notice':'既知形式の簡易検査です。秘密・個人情報の目視確認、依存脆弱性、ライセンス確認は別途必要です。'}
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/publication-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if issues:raise SystemExit(1)

if __name__=='__main__':main()

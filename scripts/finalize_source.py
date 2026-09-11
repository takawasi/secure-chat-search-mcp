"""初版の分割コミットを、検証する通常ソースへ整える。
同じ入力に繰り返し適用しても変化しない。結果のソースも公開する。
"""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
prepare = ROOT / 'scripts/release_prepare.py'
text = prepare.read_text(encoding='utf-8')
old = "    if old in text:\n        if text.count(old)!=1:"
new = "    if new in text:\n        return\n    if old in text:\n        if text.count(old)!=1:"
if new not in text:
    if old not in text:
        raise RuntimeError('準備スクリプトの形式を確認してください。')
    prepare.write_text(text.replace(old, new), encoding='utf-8')
runpy.run_path(str(prepare), run_name='__main__')
map_path = ROOT / 'docs/FILEMAP.md'
text = map_path.read_text(encoding='utf-8')
row = '| `src/secure_chat_search/integration.py` | 既存MCPの検証済み本人・既存allow-list・現在参加を引き継ぐ埋込用アダプター |\n'
if row not in text:
    anchor = '| `src/secure_chat_search/chatwork.py`'
    text = text.replace(anchor, row + anchor)
    map_path.write_text(text, encoding='utf-8')
# 実行対象ソース全体の指紋を最後に確定する。
runpy.run_path(str(prepare), run_name='__main__')

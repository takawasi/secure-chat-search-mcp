"""CI実結果を日本語で公開する。成功・失敗・未実施を混同しない。"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=ROOT/'artifacts'
EVIDENCE=ROOT/'docs/evidence'
EVIDENCE.mkdir(parents=True,exist_ok=True)
needs=json.loads(os.environ.get('NEEDS_JSON','{}'))
sha=os.environ.get('GITHUB_SHA','unknown')
repo=os.environ.get('GITHUB_REPOSITORY','takawasi/secure-chat-search-mcp')
run_id=os.environ.get('GITHUB_RUN_ID','unknown')
run_url=f'https://github.com/{repo}/actions/runs/{run_id}'
labels={'tests':'SQLite／PostgreSQL 回帰テスト','end_to_end':'実HTTP MCP＋Chromium画面操作','semantic':'実Embeddingモデルの意味検索','container':'Dockerの非root起動'}
lines=['# 検証記録','',f'検証元コミット：`{sha}`',f'実行記録：[GitHub Actions]({run_url})','',
       '結果は合成データによる参照実装の検証です。顧客環境での統合検収・本番安全性の保証ではありません。','',
       '| 検証 | 実行結果 |','|---|---|']
for key,label in labels.items():
    result=needs.get(key,{}).get('result','未実施')
    lines.append(f'| {label} | {result} |')
all_passed=bool(needs) and all(needs.get(key,{}).get('result')=='success' for key in labels)
lines += ['', '**公開PoCの自動検証はすべて成功しました。**' if all_passed else '**失敗・中断・未実施の項目が残っています。完了とは扱っていません。**','', '## 回帰テストの実測','']
lines += ['公開前点検は各回帰テストジョブの静的確認で実行し、秘密らしい文字列・禁止ファイル・壊れた相対リンクの指摘がないことを確認します。これは目視確認・依存ライセンス・脆弱性確認の代わりではありません。', '', '検索・`fetch`・保護された原文表示・期間取得・MCPの期間取得は、同じ現在ACLを通す回帰を含みます。退室後の既知IDを別入口から再取得できないことを確認します。']
for backend in ('sqlite','postgres'):
    root=ARTIFACTS/f'tests-{backend}'
    junit=next(iter(root.rglob('junit.xml')),None) if root.exists() else None
    if junit:
        tree=ET.parse(junit)
        suites=list(tree.getroot().iter('testsuite'))
        counts={key:sum(int(float(s.get(key,'0'))) for s in suites) for key in ('tests','failures','errors','skipped')}
        lines.append(f'- {backend}: {counts["tests"]}件、失敗{counts["failures"]}、エラー{counts["errors"]}、スキップ{counts["skipped"]}。')
        shutil.copy2(junit,EVIDENCE/f'{backend}-junit.xml')
    else:
        lines.append(f'- {backend}: JUnit実行記録を取得できていません。')

# 実行したソースの一致を確認する。ソース指紋はコード・テスト・スクリプト・起動設定から生成。
source=ROOT/'.verification/source.json'
source_info=json.loads(source.read_text()) if source.exists() else None
fingerprints=[]
for item in ARTIFACTS.rglob('source.json') if ARTIFACTS.exists() else []:
    info=json.loads(item.read_text())
    fingerprints.append(info.get('sha256'))
    shutil.copy2(item,EVIDENCE/(item.parent.name+'-source.json'))
if source_info:
    shutil.copy2(source,EVIDENCE/'source.json')
    same=bool(fingerprints) and all(value==source_info['sha256'] for value in fingerprints)
    lines += ['', '## 検証したソース','',f'コード指紋（SHA-256）：`{source_info["sha256"]}`',
              f'各ジョブとの一致：{"確認済み" if same else "記録不足または不一致"}。',
              '初版の分割追加を整合させた後の通常ソースを検証しています。公開版にも同じソースを保存し、起動時の動的な書き換えは行いません。']
    if not same:
        all_passed=False

assets=ROOT/'docs/assets'
assets.mkdir(parents=True,exist_ok=True)
for image in ARTIFACTS.rglob('demo-*.png') if ARTIFACTS.exists() else []:
    shutil.copy2(image,assets/image.name)
for name in ('browser-verification.json','mcp.json','semantic.json','container.json'):
    matches=list(ARTIFACTS.rglob(name)) if ARTIFACTS.exists() else []
    if matches:
        shutil.copy2(matches[0],EVIDENCE/name)
        value=json.loads(matches[0].read_text())
        lines += ['', f'## {name}','', '```json',json.dumps(value,ensure_ascii=False,indent=2),'```']

lines += ['', '## 実際の画面','']
for name,title in [('demo-sales.png','営業：過去と最新の経緯'),('demo-development.png','開発：利用者による権限差'),('demo-mobile.png','モバイル表示')]:
    if (assets/name).exists():
        lines.append(f'[{title}](assets/{name})')
lines += ['', '## 未実施：顧客環境への統合','',
          '実Chatwork OAuth、管理者エクスポート実形式、Google Workspace本人確認、Firestore／Secret Manager実接続、ChatGPT Businessへの登録、Cloud Run実デプロイは未実施です。実データの検索精度・負荷・保持削除・運用監視・第三者監査も別途検収が必要です。',
          '', '## 再現と配布','',
          'READMEの手順でローカル起動できます。テスト・実モデル・画面操作はscriptsとtestsに同梱しています。実行時のソース、検証記録、画面を含むZIPはこのActions実行のArtifactsに保存します。モデル重み、実DB、秘密情報は含みません。','']
(ROOT/'docs/VERIFICATION.md').write_text('\n'.join(lines),encoding='utf-8')
plan=ROOT/'docs/PLAN.md'
text=plan.read_text(encoding='utf-8')
if all_passed:
    text=text.replace('- [ ] P8：','- [x] P8：')
plan.write_text(text,encoding='utf-8')
readme=(ROOT/'README.ja.md').read_text(encoding='utf-8')
images=[]
if (assets/'demo-sales.png').exists():
    images += ['![営業の履歴検索デモ](docs/assets/demo-sales.png)', '', '[開発担当の表示](docs/assets/demo-development.png) · [スマートフォン表示](docs/assets/demo-mobile.png) · [検証記録](docs/VERIFICATION.md)']
readme=readme.replace('<!-- DEMO_IMAGES -->','\n'.join(images))
(ROOT/'README.md').write_text(readme,encoding='utf-8')
print(json.dumps({'all_checks_passed':all_passed,'verification':str(ROOT/'docs/VERIFICATION.md'),'run':run_url},ensure_ascii=False))

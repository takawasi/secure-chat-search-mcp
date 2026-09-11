# Work / Codex への引継ぎ

## 最終指示
日本語の説明・ファイルマップ・計画を先に作り、汎用PoCを実装して検証した。
この会話ではGitHubにpush・コミット・PR作成をしていない。
予定先は `takawasi/secure-chat-search-mcp`。ユーザーがWork側で公開を進める想定。

## ファイルを探す順番
1. ChatGPT Libraryで `secure-chat-search-mcp-v0.1.0.zip` を探す。ファイル名・版・ハッシュを確認する。
2. 見つかった保存済みZIPを、必要なときだけそのWorkの作業環境へmaterializeする。
3. Libraryで確認できなければ、元会話に添付された同名ZIPを使う。

**Libraryと各会話の一時作業フォルダは別。`/mnt/data`にないことだけで、別モードの保存物が見えないと判断しない。**
この文書は保存操作の成功を証明するものではない。実際のLibraryエントリー・添付を確認して取り出す。

## ZIPからの再開
ルートは `secure-chat-search-mcp/`。入口は `START_HERE.md` → `README.md`。
`MANIFEST.sha256` で展開した内容を確認する。
Python仮想環境は同梱しない。依存パッケージを導入して起動する。
顧客データ・本番キー・実OAuth設定は同梱していない。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python scripts/http_check.py
python scripts/check_publication.py
chat-search serve
```

## 公開前にすること
既存リポジトリを読み、現在のmain・README・ライセンス・既存コミットを確認する。空だという過去の情報を信用して上書きしない。
ユーザーの公開指示に従い、必要なら作業ブランチを作る。履歴のforce pushはしない。
`PUBLICATION_CHECKLIST.md`、秘密ファイル走査、実データ混入の目視確認、依存ライセンス方針を確認する。
公開するのはソース・架空データ・資料・検証記録。`.venv`、`data`、`.env`、秘密ファイル、ビルド一時物は除外する。
同梱のwheelは配布補助であり、通常はリポジトリ本文へコミットしない。

README内の画面画像も一緒に反映する。CIは設定ファイルを同梱しただけで、ここではGitHub Actionsを実行していない。
公開後はURLと画像・起動説明を再確認してから、`docs/07_提案文.md` の参照先として使う。

## 次に顧客環境で行う作業
実CSVの変換、既存MCP・Google/Firestore認可・OAuthストア接続、Chatwork実同期、Cloud SQL/Cloud Run配置、ChatGPTの実接続を検証する。
現在のデモをそのまま実データで公開しない。
PostgreSQL、実モデル、実クラウド、Dockerビルドなどの未検証範囲は `reports/検証結果.md` が正本。

## 変更時の優先順位
社員が探している経緯へ根拠付きで到達できるかを第一にする。
権限の漏れ・誤った履歴統合・実際の業務質問への不足を直し、仕様書や検査だけを増やして停滞しない。
計画は新しい実態に更新し、過去の仮説は根拠が変われば捨てる。変更と検証結果を `docs/09_作業記録.md` に残す。

# secure-chat-search-mcp
## ことのは検索 — 「前に、どう決めた？」を、見える範囲だけで探す。

社員が許可されたチャットの過去・最新ログから、判断の経緯と原文を探すための日本語PoCです。
既存のChatGPT／MCPと認証を捨てず、履歴検索部分を追加するための参照実装として作りました。

**v0.1.0：応募用の汎用PoC。架空データで動きます。顧客の実データ・コード・認証情報は含みません。**
**本番認証を含む完成品、実Chatworkへの接続実績、ChatGPT実画面での接続認証済み製品ではありません。**

![日本語の検索画面と原文表示](reports/01_検索と根拠.png)

[画面付きの実装概要](docs/概要.html)

## 最初の5分

Python 3.11以上を用意し、このREADMEがあるフォルダで実行します。初回のパッケージ取得にはインターネット接続が必要です。今回の検証環境はPython 3.13.5です。

macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
chat-search serve
```

Windows PowerShell:
```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\chat-search.exe serve
```

ブラウザで `http://127.0.0.1:8000` を開きます。初回起動時に架空データを `data/chat-search.db` に作成します。
依存関係を入れた後は `scripts/start.sh` または `scripts/start.ps1` でも起動できます。
`--port 8001` のようにポートを変更できます。終了はCtrl+Cです。

デモを動かすだけなら、Chatworkアカウント・Google Cloud・OpenAIのAPIキー・モデルダウンロードは不要です。
同じデモを開く人は架空利用者を自由に切り替えられます。**実データを入れないでください。**

## 見せる順番

1. 「青木｜営業」で `納期` を検索する。2024年の検討案と決定を見比べ、原文・発言日時・取得元を確認する。
2. 「佐藤｜開発」へ切り替える。同じ検索から営業の情報が消える。`障害` で開発の会話が見つかる。
3. 青木に戻し、`星野商事` を検索して「担当ルームを退室」を押す。検索と原文の両方から除外され、「復帰」で再表示される。
4. 「新着発言を追加」でDBへ保存した新着を検索する。「同じCSVを再取り込み」で重複しないことを確認する。
5. 「欠損警告を模擬表示」で同期状態の注意を確認する。このボタンは警告表示の模擬操作で、実際に履歴を削除していない。

UIの回答は原文です。架空の生成AI回答を表示して、LLM連携済みのように見せることはしていません。

## 実装されているもの

| 項目 | この配布物の状態 |
|---|---|
| 日本語画面・利用者切替・根拠表示 | 実装。画面操作を検証 |
| 共通ACL | サーバー側でtenant・本人・現在の参加グループ・承認済み対象を確認。検索前にSQLで絞る |
| 本文・期間取得 | `fetch`でも再認可。期間取得はページ送りを返し、上位検索と混同しない |
| CSV | 日本語／英語の正規化列、UTF-8/CP932、原子的取り込み、再実行、明示的な削除行 |
| IDあり／IDなし履歴 | IDありは統合。IDなしは明示したルームのスナップショット置換。曖昧なAPI同一視をしない |
| 最新同期 | 実Chatwork向け読取アダプター、`force=1`、重複排除、旧版無視、100件未知の警告 |
| Webhook | 実仕様の署名検証、DB永続キュー、二重受付排除、単一worker処理、失敗最大5回 |
| MCP | 読取専用 `search` / `fetch` / `read_timeline`。ステートレスHTTP JSON応答 |
| 文字列・関連語検索 | 日本語NFKC正規化＋部分一致＋小さな明示辞書。SQLの`%`・`_`は文字として扱う |
| 意味検索 | 任意のEmbedding HTTPサービスに明示接続した場合のみ有効。ベクトル比較＋RRFを実装。実モデル未接続 |
| 認証統合口 | RS256の専用JWT検証、現在のChatwork本人・参加ルーム確認、既存トークン管理への接続口 |
| DB | SQLiteで動作確認。SQLAlchemyによるPostgreSQL接続設定とDDL確認あり。実PostgreSQLは未検証 |
| 起動・運用資料 | Docker設定、Cloud Run構成案、統合手順、公開前確認、Work引継ぎ |

**既定の関連語検索は、AIの意味検索ではありません。** 例えば「納期・期限・納品」を同じ辞書群として扱います。
意味検索を設定した場合も、許可済みの最大2,000件をアプリ側で比較する小規模方式です。永続ベクトル索引・`pgvector`/`pg_bigm`は今回の実装に含みません。

## 構成

```text
日本語の確認画面 / 既存MCP / 本配布物のMCP
                  │
       本人確定 → 共通ACL → SearchService
                  │
          SQLite / PostgreSQL接続口
             ▲               ▲
     正規化CSVインポート   最新API / Webhookキュー
```

主な入口は `src/secure_chat_search/search.py` です。UIとMCPは同じ処理を利用します。
既存MCPに同じPythonプロセスで組み込める場合は、このクラスを接続し、新しい認証画面を増やさない案を推奨します。

## 検証する

```bash
python -m pytest -q
python scripts/http_check.py
python scripts/check_publication.py
```

カバレッジ付き:
```bash
python -m pytest --cov=secure_chat_search --cov-report=term-missing
```

ブラウザ操作試験（Chromiumの準備が必要）:
```bash
python -m playwright install chromium
python scripts/ui_check.py
```

インストール済みChromiumを使う場合は `CHROMIUM_PATH` を指定できます。
この会話の実行環境ではブラウザのHTTPアクセスが管理ポリシーで遮断されたため、画面試験は
`python scripts/ui_check.py --asgi-bridge` で実HTML/CSS/JSとFastAPI TestClientをつないで実施しました。
ブラウザから実HTTPを経由する通し試験とは分け、実HTTPサーバーは別の `http_check.py` で確認しています。
詳しい結果と未検証範囲は [検証結果](reports/検証結果.md) にあります。

## CSVを試す

`examples/history.csv` は**このPoCの正規化形式**です。Chatwork管理者CSVの実出力そのものではありません。
顧客CSVは、列・日時・削除表示・編集履歴を確認してこの形式へ変換します。

```bash
chat-search import examples/history.csv --tenant demo --snapshot-at 2026-09-01T04:00:00+09:00
chat-search import examples/history_idless.csv --tenant demo --snapshot-at 2026-09-01T04:00:00+09:00 --replace-room 500
```

先にデモサーバーを一度起動して、ルームを作成してください。
IDなしCSVは部屋の完全スナップショットを一括で指定します。分割CSVを同じ部屋へ順番に置換すると、前の分割分が消えます。
上限を超えるIDなし履歴は、実データ対応時にステージング→一括切替へ拡張する対象です。

## 資料

[目的と範囲](docs/01_目的と範囲.md) / [ファイルマップ](docs/02_ファイルマップ.md) / [先に作った計画](docs/03_実装計画.md)

[設計](docs/04_設計.md) / [Chatwork仕様と制限](docs/05_Chatwork仕様と制限.md) / [既存PoCへの統合](docs/06_既存PoCへの統合.md)

[提案文](docs/07_提案文.md) / [運用とセキュリティ](docs/08_運用とセキュリティ.md) / [Workへの引継ぎ](WORK_HANDOFF.md)

## 公開・利用上の境界

デモの役割切替、連携用JWT、ChatworkのOAuthトークンは別の仕組みです。
実データを扱う前に、認証統合・CSV照合・削除反映・同期負荷・クラウド権限・監視を検証してください。
`SECURITY.md` と `PUBLICATION_CHECKLIST.md` を参照してください。
この会話からGitHubへは書き込んでいません。再利用条件のライセンスは未指定です。

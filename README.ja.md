# Secure Chat Search MCP

**社員が閲覧してよい過去のチャットを、最新の発言とつなげて根拠付きで検索する参照実装です。**

既存のChatGPT／MCPの入口を捨てず、背後へ履歴取り込みと権限制御付き検索を追加するための公開PoCです。説明・画面・エラーメッセージは日本語を基本にしています。

> **このリポジトリのデモは全件合成データです。** 会社・人物・会話は架空です。顧客のコード・秘密情報・Chatworkログを含みません。ChatGPT Business、Google Workspace、実Chatwork OAuth、顧客GCP環境への統合が済んだ完成品ではありません。

<!-- DEMO_IMAGES -->

## 最初に見るもの

| 目的 | 入口 |
|---|---|
| 動かして評価する | このREADMEの「起動」と「3つの確認」 |
| 何を追加する提案か | [構成と統合方針](docs/INTEGRATION.md)・[提出用の提案概要](docs/PROPOSAL.md) |
| Chatwork固有の制約 | [取得・同期・削除の扱い](docs/CHATWORK.md) |
| 実装場所を探す | [ファイルマップ](docs/FILEMAP.md) |
| 検証の実行結果と限界 | [検証記録](docs/VERIFICATION.md)・GitHub Actions |
| 引き継いで開発する | [実装計画](docs/PLAN.md)・[設計](docs/ARCHITECTURE.md)・[運用](docs/OPERATIONS.md) |

## 起動：APIキー不要

Python 3.11以上を使用します。CIの基準環境はPython 3.12です。

```bash
git clone https://github.com/takawasi/secure-chat-search-mcp.git
cd secure-chat-search-mcp
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
secure-chat-search serve
```

ブラウザで **http://127.0.0.1:8000** を開きます。初回にローカルSQLiteへ合成データを作成します。外部AIへの送信やAPI課金は行いません。Windows PowerShellの仮想環境有効化は `.venv\Scripts\Activate.ps1`、依存導入は `python -m pip install -e ".[dev]"` です。

Dockerでは次の操作で起動できます。公開ポートはホストのループバックへ限定しています。

```bash
docker compose up --build
```

**デモ認証は公開された架空の利用者を切り替える機能です。実データの保護には使えません。** 実データには別DBと`gateway`モードを使います。Cloud Run上のデモモードは起動時に拒否します。

## 3つの確認

**1．過去と最新をつなぐ。** 佐藤（営業）で「納期」を検索すると、2023年の希望・変更案・合意と、2026年の別案件が出ます。発言を開き、日時・発言者・前後の会話を確認します。画面は検索の検証用であり、AI回答生成を装った定型文は表示しません。

**2．人によって見える内容が違う。** 佐藤で「予算」を検索しても、開発内部の予算は出ません。鈴木（開発）へ切り替えると表示されます。利用者変更時には前の人の本文表示を消します。

**3．一度見つかったIDでも、退室後には取得できない。** 佐藤で「納期」の本文を開き、「担当ルームから退室」→「直前の本文を再取得」を押します。新しい検索だけでなく、本文の再取得も拒否します。再参加・最新ログ追加・初期化も画面から試せます。変更されるのは合成データだけです。

## 実装した範囲

| 項目 | この公開版 |
|---|---|
| 過去ログ | 正規化CSV＋対象期間マニフェストを全件検証後に一括反映。実Chatwork CSVの最終マッピングは実サンプルで確定 |
| 重複・編集・削除 | 安定IDの更新、IDなし履歴のスナップショット入替、一意な重複の照合、明示削除時の本文・ベクトル消去 |
| 最新同期 | 読取専用Chatwork APIアダプター、最新100件の重複取得、重複除去、欠損疑いの記録 |
| Webhook | 署名検証→DB受信箱へ保存→HTTP 200。処理は別コマンド。再送・削除通知があるとは仮定しない |
| 権限制御 | 利用者allow-list、現在参加ルーム、会社が承認したグループの積集合。検索・本文・期間取得のすべてで再確認 |
| 日本語文字列検索 | NFKC正規化、部分一致、複数語AND、ルーム・日時・発言者絞り込み。SQLはバインド変数を使用 |
| 意味検索 | 任意導入の実ローカル多言語Embedding、コサイン類似度、キーワードとのRRF併用。疑似ベクトルへの代替なし |
| MCP | 公式Python SDKのStreamable HTTP。`search`・`fetch`・`read_room_period` |
| DB | SQLAlchemyでSQLite／PostgreSQLに対応。専用`pgvector`／`pg_bigm`索引はこの版では未使用 |
| 検証 | 回帰テスト、公式MCPクライアント往復、実HTTPブラウザ操作、PostgreSQL、実Embeddingモデル、DockerのCI |

## 意味検索を有効にする

既定は軽量なキーワード検索です。意味検索は、**実際のモデルを導入して索引を作る操作を明示的に行った場合だけ**使用できます。

```bash
python -m pip install -e '.[semantic]'
secure-chat-search init
export SCS_SEMANTIC_ENABLED=true
secure-chat-search index
secure-chat-search serve
```

PowerShellでは `export` の代わりに `$env:SCS_SEMANTIC_ENABLED="true"` を使います。初回は公開モデルを取得するため外部通信とディスク容量が必要です。取得後のEmbeddingはローカル処理です。モデルは `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`。モデル自体の利用条件も確認してください。

索引は本文更新時に無効化されます。同期後には`index`を再実行します。未索引件数は応答に表示し、黙って意味検索済みとして扱いません。小規模PoCの厳密な全件比較のため、意味検索対象は既定5,000発言まで。超えたら期間・ルームを絞ります。大規模運用では日本語索引・ベクトル索引・分割方法を別途設計します。

## MCP接続

起動したサーバーのMCP入口は **`http://127.0.0.1:8000/mcp/`** です。デモ利用者のBearerは`demo:alice`と`demo:bob`。これは秘密ではなく、合成データ専用の公開識別子です。

| ツール | 用途 |
|---|---|
| `search` | 関連する発言を探す。権限条件を付けて検索する |
| `fetch` | 検索結果IDの本文と同じルームの前後文脈を取得。改めて認可する |
| `read_room_period` | 指定期間を時系列・ページ単位で読む。上位検索結果を期間全体と取り違えない |

```bash
python scripts/mcp_smoke.py
```

**ChatGPTへURLを登録するだけで顧客OAuthまで完成するものではありません。** 既存MCPに検索部品を組み込む方法を第一候補とし、別サービスにする場合は専用audienceの短命JWTを既存認証ゲートウェイから発行します。詳細は[統合手順](docs/INTEGRATION.md)に記載しています。

## テスト

```bash
python -m pytest -q
python -m compileall -q src
python -m playwright install chromium
# 別ターミナルで serve を起動した状態で実行
python scripts/mcp_smoke.py
python scripts/browser_smoke.py
# モデル取得を含む任意の実モデル検証
python scripts/semantic_smoke.py
```

CIは実行結果・スクリーンショット・検証時のソースをArtifactsに残します。実行した検証と未実施の外部統合は[検証記録](docs/VERIFICATION.md)で分離します。CI実行に依存する公開証跡は、Actionsの対象コミットも合わせて確認してください。

## 「安全」「完全」の境界

この版はセキュリティ認証を受けた製品ではありません。現在の参加権限を確認してから新しく取り出す情報を制限しますが、**すでにChatGPTへ渡った文章の回収は保証しません**。また、過去ログの全件取得、削除の即時反映、添付ファイル本文検索、自動の管理者エクスポート取得、OAuth更新、本番監視・バックアップ運用の完成は保証しません。

本番導入時には、実CSVのIDと編集削除表現、対象規模、会社の保持方針、同期遅延、Google本人確認とChatworkアカウントの対応、既存allow-listの反映を検証します。[Chatworkの制約](docs/CHATWORK.md)・[セキュリティ境界](docs/SECURITY.md)を参照してください。

## 公開とライセンス

公開リポジトリであることと、無制限の再利用許諾は同じではありません。作者の指定なしにMIT等を付与していません。[ライセンス方針](LICENSE_POLICY.md)と、利用する依存ライブラリ・モデルの個別条件を確認してください。

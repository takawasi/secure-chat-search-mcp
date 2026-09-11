# 既存PoCへ追加する構成

## 最初に残すもの

ChatGPT Business、既存MCP、Google Workspace本人確認、ユーザー別Chatwork OAuth、Firestore allow-list、既存Secret Managerを原則残します。このリポジトリの目的は、それらの作り直しではなく、履歴の取得・保存・検索を追加することです。

## A．既存MCP内へ検索部品を組み込む（第一候補）

`embedded`モードはPythonライブラリとして使うための設定です。独立したHTTPサーバーを起動できません。既存コードで検証済みのGoogle `sub`を`Principal`へ渡します。メール文字列やLLMのツール引数から本人を決めません。

```python
from secure_chat_search.core import Database, Principal, Settings
from secure_chat_search.integration import ExistingPoCAuthorizer
from secure_chat_search.search import SearchService

settings = Settings(
    mode="embedded",
    database_url="postgresql+psycopg://...",  # 実値は秘密管理から取得する
    public_url="https://your-internal-search.example",
)
db = Database(settings)
db.initialize()

# この2つは既存PoCに接続する箇所。
# current_rooms(session, user) は、その利用者の現在のChatwork参加を確認してset[str]を返す。
# enabled_checker(sub) は、Firestore等の既存allow-listを毎回確認してboolを返す。
authorizer = ExistingPoCAuthorizer(
    membership_provider=existing_current_room_provider,
    enabled_checker=existing_allowlist_check,
)
service = SearchService(db, authorizer, settings)

# MCPの認証済みリクエスト処理内で呼ぶ。verified_google_subは既存の検証済み状態から取得する。
result = service.search(Principal(verified_google_sub), query="納期")
```

上記は接続位置を示す例です。未受領の顧客関数を実装済みと扱っていません。既存PoCのトークン保管方法へ合わせた`MembershipProvider`とallow-list確認を実装してください。読取API用の`LiveMemberships`／`CredentialStore`は独立gateway用の参考アダプターです。

既存のツール名や応答形式を維持する場合も、検索・本文取得・前後の会話・期間取得を同じ`SearchService`へ集約します。IDなし履歴の根拠URLを開く認可済み`/records/{id}`等のルートは、既存のSSO付きポータルへ追加してください。ユーザーがブラウザで開けない保護URLを、確認可能な根拠として提示しないことが必要です。

## B．独立HTTPサービスとして追加する

`gateway`モードは、既存MCPから呼ぶ別サービスとして利用します。次を設定します。

| 設定 | 内容 |
|---|---|
| `SCS_MODE` | `gateway` |
| `SCS_DATABASE_URL` | デモとは別の専用DB |
| `SCS_PUBLIC_URL` | 社内向けHTTPS origin |
| `SCS_ALLOWED_HOSTS` | そのホスト名。ワイルドカード不可 |
| `SCS_JWT_ISSUER` / `SCS_JWT_AUDIENCE` | この呼び出し専用のissuer・audience |
| `SCS_JWT_PUBLIC_KEY_FILE` | 2048bit以上RSA公開鍵のマウント先 |
| `SCS_CREDENTIALS_FILE` | 既存PoCが安全に更新するChatwork認証情報のマウント先 |

既存MCPは認証・allow-list確認後に、専用audience、安定したsub、iat、nbf、expを含む有効期間5分以内のRS256 JWTを発行します。このサービスは署名・issuer・audience・期限を検証します。ChatGPT→MCPのトークン、Google ID Token、Chatworkアクセストークンを、そのまま別のサービスのBearerとして使い回しません。

このリポジトリにOAuth認可画面・認可コード交換・refresh実装はありません。既存PoCにあるものを使います。ブラウザ用のgatewayログイン画面も実装していないため、原文表示は既存SSOリバースプロキシ等を介して同じ認可を適用してください。JWTをURLに付ける方式は避けます。

認証情報ファイルの参照形は以下です。**これは構造例です。実値はGitに置かないでください。**

```json
{
  "google-sub-of-employee": {
    "account_id": "123456",
    "access_token": "既存の秘密管理から供給する値",
    "expires_at": 1900000000
  }
}
```

読み取るたびに有効期限とアカウント対応を確認します。期限切れは503で停止し、勝手に別ユーザーのトークンへ切り替えません。実運用のSecret Manager方式・ユーザー数・更新頻度に合わせ、この参考ファイルアダプターを置き換えてください。

## GCPへ追加するもの

| 役割 | 候補 |
|---|---|
| 履歴原本の受領 | 検索サービスから分離した非公開Cloud Storage。保持期限を設定 |
| メッセージと索引・同期状態 | Cloud SQL for PostgreSQLを第一候補。実件数で方式・費用を決める |
| インポート・同期・索引作成 | Cloud Run Jobs。既存Schedulerから起動 |
| MCP入口 | 既存Cloud Runを維持。必要なら検索サービスだけ分離 |
| 秘密情報 | 既存Secret Manager。役割ごとに最小権限 |
| 監視 | 最終同期、欠損疑い、認証失効、取り込み失敗、Webhook停止を監視へ接続 |

上記は導入案です。顧客GCPへの実デプロイ、課金設定、IAM・ネットワーク・バックアップの検収は実施済みではありません。

## 導入時に受領するものと手順

1. 既存コード・認証フロー・構成図・実CSVサンプル・対象規模・実際の質問例を確認する。
2. 現在参加なら参加前履歴も許可するか、編集削除の反映期限、添付検索の要否を合意する。
3. 利用者対応と承認グループを構成し、同期を先に開始して履歴との重複期間を確保する。
4. 正規化CSVを取り込み、件数・時点・重複・除外を検証する。
5. 既存MCPへ検索・本文・期間取得を追加し、社員2名以上の権限差・退室・allow-list無効化を確認する。
6. 顧客の実質問で精度・速度・引用の利用可能性を確認し、本番へ残る運用課題を記録する。

この6項目は顧客統合の検収です。公開デモのテスト成功で代用しません。

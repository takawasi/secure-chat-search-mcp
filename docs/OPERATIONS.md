# 起動・運用・復旧

## デモ

READMEの仮想環境から`secure-chat-search serve`を実行します。DBは既定で`.data/demo.sqlite3`。画面の初期化操作は合成データを入れ直します。実データを含む場所をデモ用DBに指定しないでください。実データ・モデルの重み・トークンはGit管理しません。

## 正規化CSVの見本

```bash
secure-chat-search sample-export --output sample_data
```

`history.csv`と`manifest.json`を作成します。これは再現可能な架空データです。顧客の実CSVの出力形式を証明するサンプルではありません。

## 実データ用の初期設定

独立HTTPでは`SCS_MODE=gateway`と専用DB・JWT公開鍵・認証情報マウント・issuer/audienceを設定します。既存MCP内に組み込む場合は`embedded`を使い、認証済みコンテキストからのみ検索部品を呼びます。手順は[INTEGRATION](INTEGRATION.md)を参照してください。

```bash
secure-chat-search init
secure-chat-search configure sample_data/provision.example.json
secure-chat-search import-csv /private/export.csv /private/manifest.json
```

`configure`は指定した利用者・ルームを更新します。リストに載っていない利用者を自動削除しません。利用停止は`enabled:false`、対象外ルームは`approved:false`を明示します。既存Firestoreのallow-listを正本とする場合は、組み込みアダプターの確認関数へ接続し、ローカル構成と二重の権限源を放置しません。

## 同期と索引

```bash
# 独立gatewayの参考認証ストアを利用する場合
secure-chat-search sync --user GOOGLE_SUB --room 123456
secure-chat-search drain-webhooks
SCS_SEMANTIC_ENABLED=true secure-chat-search index
```

同期する本人が現在そのグループに参加していることを確認します。収集担当者の退室・認証解除は、停止として検出してください。初版は同期・受信箱反映・索引を管理者側の単一ジョブ列で実行する運用を基本とします。

Cloud Scheduler→Cloud Run Jobsに対応させる場合は、実コード受領後にトークン更新・API制限・収集担当者の割り当て・同時実行を設計します。本リポジトリは顧客側のSchedulerやCloud Runを作成・変更していません。

## 日常確認

| 観測するもの | 対応 |
|---|---|
| `last_poll_at`が更新されない | ジョブ停止、認証期限、ルーム退室を確認 |
| `sync_error=auth_required` | 既存OAuthの更新・再認証を確認。別ユーザーへ無断で切り替えない |
| `sync_error=rate_limited` | API呼び出し総数と間隔を見直す。無限再試行しない |
| `gap_suspected=true` | 欠けた可能性のある期間を特定し、管理者エクスポートで補修 |
| CSVの形式・日時エラー | 全体を反映しない。変換処理とマニフェストを直して再実行 |
| 古いスナップショットの拒否 | より新しい履歴を用意。時刻を偽って通さない |
| 意味検索の未索引件数 | `index`を実行。モデル変更時は再索引 |
| 意味検索の候補上限 | ルーム・期間を絞る。大規模化は索引方式を別途導入 |
| Webhook反映失敗 | 受信箱のエラーコードを確認。API／再エクスポートで状態を補修 |

ルームの状態はそのルームを見られる利用者だけに返します。監査には主体・操作・成否・件数を残します。検索語、メッセージ本文、OAuthトークンを監査用フィールドへ追加しないでください。

## 保持・復元・並列性

CSV原本は必要最小限の期間だけ保管し、検索サービスから分離します。DBの論理削除とバックアップ等の物理消去を分けて運用します。復元後はデータ取得時点を表示し、権限を現在のサービスから再確認してから公開してください。

PoCの期間ページングは、更新中のDBを固定した長時間スナップショットではありません。厳密な監査用全件出力が必要なら、取込世代固定や一貫した読取トランザクションを追加します。大量並列ワーカー、分散ロック、移行バージョン管理、HA、自動復旧は未完成の本番領域です。

## ポート・DB・Docker

ローカル起動は127.0.0.1を既定とします。Dockerコンテナ内では0.0.0.0へバインドしますが、Composeのホスト側は127.0.0.1:8000に限定します。`SCS_PUBLIC_URL`は実際にブラウザが利用するoriginと合わせてください。

通常のDockerイメージはPostgreSQLドライバーを含みます。意味検索もコンテナに入れる場合は、`docker build --build-arg EXTRAS=postgres,semantic -t secure-chat-search-mcp .`で作成し、モデルのキャッシュを専用ボリュームへ置きます。モデル取得失敗を偽の検索結果で隠しません。

## 検証の再実行

`pytest`は通常の一時SQLiteを使用します。`SCS_TEST_DATABASE_URL`を指定した場合、末尾が`/scs_test`の専用DBに限りテスト対象テーブルを作り直します。**本番DBを指定しないでください。** CIは使い捨てPostgreSQLサービスで実行します。

CIが生成した`.verification/`、画面、Junit XML、依存一覧、配布ZIPは実行ごとのArtifactsで確認できます。成功・失敗・未実施を[VERIFICATION](VERIFICATION.md)で区別します。

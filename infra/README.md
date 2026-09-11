# 実行基盤の設定例

## ローカルDockerデモ

```bash
docker compose up --build
```

ブラウザは `http://127.0.0.1:8000`。ホスト側公開はループバックだけ。
コンテナ内で待受アドレス0.0.0.0を使うため、composeにデモ用の明示許可を設定している。
データはnamed volumeに保持する。`docker compose down`で停止し、データの削除が必要な場合だけ `down -v` を使う。
今回の作業環境にはDockerがないため、**Dockerビルドと起動は未検証**。

## PostgreSQLへ接続する場合

```bash
python -m pip install -e '.[postgres]'
export SCS_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME'
chat-search init-db
```

SQLAlchemyのPostgreSQL用DDL生成は自動試験しているが、実DBに接続した試験は行っていない。
DB権限、TLS、接続数、トランザクション、バックアップ、索引、性能は対象環境で確認する。
`pgvector`や`pg_bigm`を有効化するだけでこのアプリが自動的に索引を使うわけではない。
この版は部分一致・アプリ側ベクトル比較の小規模方式。大規模化時に検索実装を変更する。

## Google Cloudへの追加案

```text
既存Cloud Run MCP（認証・Firestore allow-listを維持）
        │ SearchServiceの直接呼出し または署名付き内部通信
        ▼
Cloud Runの検索サービス ─ Cloud SQL PostgreSQL
        ▲                       ▲
管理者CSV → 非公開Cloud Storage → Cloud Run Job Importer
Chatwork API ← Cloud Scheduler → Cloud Run Job Synchronizer
Chatwork Webhook → 同サービスの署名検証 → DBキュー → 単一Job worker
```

Cloud Runの一時ファイルシステムをSQLiteの永続運用先にはしない。
Cloud SQLを永続データ、Firestoreを既存の利用者管理、Secret Managerを既存認証管理として使う案。
デモ版のまま `--allow-unauthenticated` でデプロイするための自動スクリプトは付属しない。
顧客のIAM・ネットワーク・ゲートウェイ・Webhook公開口の要件を確認してから配置する。

本Dockerfileのポートは8000。Cloud Run側にもcontainer port8000を明示するか、実環境のPORTへ合わせて起動コマンドを変更する。
ヘルスチェックは `/healthz`。現状はプロセス応答で、DBのreadinessを保証するチェックではない。

## 未実施
Google Cloudリソースの作成・課金、Cloud Run配置、Cloud SQL接続、Secret Manager権限検証、Scheduler起動、監視アラート作成は行っていない。

公式資料：
https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule
https://docs.cloud.google.com/run/docs/configuring/instances/secrets
https://docs.cloud.google.com/sql/docs/postgres/extensions

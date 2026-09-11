# ファイルマップ

## 最短の読み順

製品像はREADME、評価は画面とVERIFICATION、既存構成への追加はINTEGRATION、コードは `app.py → search.py → auth.py` の順に読むと責務を追えます。

| パス | 責務 |
|---|---|
| `README.md` | 公開版の入口。起動、評価シナリオ、範囲、検証リンク |
| `README.ja.md` | READMEの編集元。CIが実在する画面へのリンクを加えてREADMEへ反映 |
| `AGENTS.md` | 引き継ぎ時の目的、作業順、公開情報の境界 |
| `pyproject.toml` | Python依存・CLIエントリーポイント |
| `src/secure_chat_search/core.py` | 設定、DBモデル、DB種別ガード、時刻・ID検証、監査、共通エラー |
| `src/secure_chat_search/auth.py` | 本人の識別、サービス利用可否、現在参加ルームと承認対象の積集合 |
| `src/secure_chat_search/integration.py` | 既存MCPの検証済み本人・既存allow-list・現在参加を引き継ぐ埋込用アダプター |
| `src/secure_chat_search/chatwork.py` | 固定ホストの読取APIと、既存PoCが管理する認証情報の読取 |
| `src/secure_chat_search/ingestion.py` | 正規化CSV、全件検証、履歴入替、ID有無の区別、更新・削除・重複照合 |
| `src/secure_chat_search/sync.py` | 最新範囲の重複取得、欠損疑い、Webhook署名・永続受信箱・反映 |
| `src/secure_chat_search/embeddings.py` | 実ローカルEmbedding、再索引、本文変更時の再確認 |
| `src/secure_chat_search/search.py` | 認可済み範囲での検索、本文と同一ルーム文脈、期間ページング、根拠メタデータ |
| `src/secure_chat_search/mcp_server.py` | 公式SDKの`search`／`fetch`／`read_room_period`。本人はHTTP認証状態から受け取る |
| `src/secure_chat_search/app.py` | HTTP入口、認証ミドルウェア、サイズ制限、デモ専用操作、保護された原文表示 |
| `src/secure_chat_search/cli.py` | 起動、構成、インポート、同期、Webhook反映、索引作成 |
| `src/secure_chat_search/demo.py` | 合成人物・ルーム・会話、デモ初期化と変更操作 |
| `src/secure_chat_search/static/` | 日本語HTML/CSS/JavaScript。本文の表示は`textContent`で行う |
| `sample_data/` | CLIから再生成できる合成CSV・マニフェスト。実データを置かない |
| `tests/conftest.py` | 分離されたDBと合成データのテスト準備 |
| `tests/test_authorization_search.py` | 越権、退室、直接取得、期間、文字列検索、意味検索の結合部分 |
| `tests/test_ingestion_sync.py` | 原子的取込、重複、更新・削除、取得不足、Webhook順序 |
| `tests/test_http_mcp_auth.py` | JWT、HTTP入口、APIアダプター、公式MCPクライアント往復 |
| `scripts/browser_smoke.py` | 実ブラウザの操作・権限差・退室の確認と撮影 |
| `scripts/mcp_smoke.py` | 実HTTPによる公式MCPクライアントの確認 |
| `scripts/semantic_smoke.py` | 実モデルを取得して意味検索・権限・再索引を確認 |
| `scripts/publication_check.py` | Library版から採用した、秘密らしい文字列・禁止ファイル・相対リンクの公開前簡易点検 |
| `scripts/release_prepare.py` | 初版公開時のソース整合処理。適用内容は小さく明示し、結果を通常のソースへ保存 |
| `scripts/publish_report.py` | CIの実結果から検証記録を生成。未実行・失敗を成功扱いしない |
| `.github/workflows/ci.yml` | SQLite／PostgreSQL、MCP、ブラウザ、実モデル、Dockerの検証 |
| `Dockerfile`・`compose.yaml` | 非rootユーザーでの再現可能な起動構成 |
| `LICENSE_POLICY.md` | 作者がライセンスを指定するまでの公開・再利用の境界 |

## 設計・運用文書

`PLAN.md` は目的と完了条件、`ARCHITECTURE.md` は処理構造、`CHATWORK.md` は外部仕様と未確定事項、`INTEGRATION.md` は既存PoCへの追加、`SECURITY.md` は認証・認可・保持の境界、`OPERATIONS.md` は具体操作、`PROPOSAL.md` は提案に使える説明、`VERIFICATION.md` は実行証跡、`LESSONS.md` は根拠と失敗から更新する教訓です。

## リポジトリへ入れないもの

`.env`、実OAuthトークン、RSA秘密鍵、顧客CSV、実DB、モデルの重み、顧客ログ、顧客名入り画像。これらは`.data/`や秘密専用マウントに分離し、公開コミットへ追加しません。公開前は `scripts/publication_check.py` を実行しますが、目視・依存ライセンス・脆弱性確認の代わりにはなりません。

"""起動・CSV取り込み・単発同期・Webhookキュー処理。"""
import argparse
import json
import os
from pathlib import Path
import sys
from .config import Settings
from .db import Database
from .errors import AppError
from .text import timestamp

def main():
    parser = argparse.ArgumentParser(description="権限を維持する日本語チャット履歴検索")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("serve", help="サーバーを起動する（既定は架空デモ）")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8000)
    sub.add_parser("init-db", help="DBのテーブルを作成する")
    directory = sub.add_parser("configure", help="信頼する利用者・承認ルームをJSONから登録する")
    directory.add_argument("file", type=Path)
    imp = sub.add_parser("import", help="正規化CSVを取り込む")
    imp.add_argument("file", type=Path)
    imp.add_argument("--tenant", required=True)
    imp.add_argument("--snapshot-at", required=True)
    imp.add_argument("--replace-room", action="append", default=[])
    imp.add_argument("--encoding", default="utf-8-sig", choices=["utf-8-sig", "cp932"])
    poll = sub.add_parser("sync", help="承認済み本人のChatwork認証で1ルームを同期する")
    poll.add_argument("--tenant", required=True); poll.add_argument("--subject", required=True)
    poll.add_argument("--room", required=True)
    sub.add_parser("drain-webhooks", help="永続Webhookキューを最大100件処理する")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        db = Database(settings.database_url); db.initialize()
        if args.command == "serve":
            import uvicorn
            from .app import create_app
            if settings.mode == "demo" and args.host not in ("127.0.0.1", "localhost", "::1") and os.getenv("SCS_ALLOW_DEMO_BIND") != "true":
                raise AppError(400, "unsafe_demo_bind", "デモを外部公開しないでください。コンテナ内部bindにはSCS_ALLOW_DEMO_BIND=trueを明示します")
            if "SCS_BASE_URL" not in os.environ and settings.mode == "demo":
                settings.base_url = f"http://127.0.0.1:{args.port}"
            uvicorn.run(create_app(settings, db), host=args.host, port=args.port, access_log=False)
            return
        if args.command == "init-db": result = {"initialized": True}
        elif args.command == "configure":
            from .directory import configure
            result = configure(db, json.loads(args.file.read_text(encoding="utf-8")))
        elif args.command == "import":
            from .ingest import Importer
            result = Importer(db).import_csv(args.file.read_bytes(), args.tenant, timestamp(args.snapshot_at), args.encoding, args.replace_room)
        elif args.command == "sync":
            from .auth import FileTokenProvider, Policy, Principal
            from .chatwork import ChatworkClient
            from .sync import Synchronizer
            if settings.mode != "integration":
                raise AppError(400, "integration_required", "実Chatwork同期は統合モードで実行します")
            client = ChatworkClient(FileTokenProvider(settings.token_file))
            result = Synchronizer(db).poll(Principal(args.tenant, args.subject), args.room, client, Policy(db, client))
        else:
            from .sync import WebhookInbox
            result = WebhookInbox(db, settings.webhook_token, settings.webhook_tenant).drain()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except AppError as e:
        print(json.dumps({"error": e.code, "message": e.message}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError) as e:
        print("設定または入力ファイルを確認してください。", file=sys.stderr)
        raise SystemExit(1) from e

if __name__ == "__main__":
    main()

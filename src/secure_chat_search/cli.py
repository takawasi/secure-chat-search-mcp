"""管理者用CLI。実データ操作はgatewayモードに限定する。"""
import argparse
import json
from pathlib import Path
from .core import Database, DomainError, Room, Settings, User, numeric_id
from .demo import sample_export, seed


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def configure(db, payload):
    with db.session() as session:
        for entry in payload.get("users", []):
            sub, name = entry["sub"], entry["name"]
            if not isinstance(sub, str) or not 1 <= len(sub) <= 160 or not isinstance(name, str) or len(name) > 200:
                raise DomainError("configuration", "利用者IDまたは名前の形式が不正です。")
            user = session.get(User, sub) or User(sub=sub)
            user.name, user.account_id = name, numeric_id(entry["account_id"])
            user.enabled = entry.get("enabled") is True
            session.add(user)
        for entry in payload.get("rooms", []):
            key = numeric_id(entry["id"])
            if entry.get("kind", "group") not in {"group", "direct", "my"} or len(entry["name"]) > 200:
                raise DomainError("configuration", "ルームの形式が不正です。")
            room = session.get(Room, key) or Room(id=key)
            room.name, room.kind = entry["name"], entry.get("kind", "group")
            room.approved = entry.get("approved") is True and room.kind == "group"
            session.add(room)
    return {"configured_users": len(payload.get("users", [])), "configured_rooms": len(payload.get("rooms", []))}


def main():
    parser = argparse.ArgumentParser(description="閲覧権限付きチャット履歴検索：管理・起動コマンド")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="HTTP/MCPサーバーを起動")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    sub.add_parser("init", help="DBを初期化。デモでは合成データも作成")
    conf = sub.add_parser("configure", help="利用者allow-listと対象ルームを構成（gatewayのみ）")
    conf.add_argument("file")
    imp = sub.add_parser("import-csv", help="正規化CSVとマニフェストを取り込み（gatewayのみ）")
    imp.add_argument("csv")
    imp.add_argument("manifest")
    sync = sub.add_parser("sync", help="現在参加中の対象ルームを重複取得（gatewayのみ）")
    sync.add_argument("--user", required=True)
    sync.add_argument("--room", required=True)
    sub.add_parser("drain-webhooks", help="受理済みWebhookを反映")
    sub.add_parser("index", help="許可対象のメッセージを実モデルで再索引")
    sample = sub.add_parser("sample-export", help="合成CSVとマニフェストを出力")
    sample.add_argument("--output", default="sample_data")
    args = parser.parse_args()
    try:
        settings = Settings()
        if args.command == "serve":
            import uvicorn
            uvicorn.run("secure_chat_search.app:create_app", factory=True, host=args.host, port=args.port, access_log=False)
            return
        if args.command == "sample-export":
            raw, manifest = sample_export()
            target = Path(args.output)
            target.mkdir(parents=True, exist_ok=True)
            (target / "history.csv").write_bytes(raw)
            (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            output({"synthetic_export": str(target)})
            return
        db = Database(settings)
        db.initialize()
        if args.command in {"configure", "import-csv", "sync"} and settings.mode not in {"gateway", "embedded"}:
            raise DomainError("gateway_required", "実データ操作にはgatewayモードと専用DBを使用してください。")
        if args.command == "init":
            if settings.mode == "demo":
                seed(db)
            output({"initialized": True, "mode": settings.mode})
        elif args.command == "configure":
            output(configure(db, json.loads(Path(args.file).read_text(encoding="utf-8"))))
        elif args.command == "import-csv":
            from .ingestion import import_csv
            if Path(args.csv).stat().st_size > 20_000_000:
                raise DomainError("export_too_large", "CSVは20MB以内で指定してください。")
            output(import_csv(db, Path(args.csv).read_bytes(), json.loads(Path(args.manifest).read_text(encoding="utf-8"))))
        elif args.command == "sync":
            from .auth import Auth, LiveMemberships
            from .chatwork import CredentialStore
            from .core import Principal
            from .sync import poll_room
            store = CredentialStore(settings.credentials_file)
            auth = Auth(settings, LiveMemberships(store))
            with db.session() as session:
                principal = Principal(args.user)
                rooms = auth.allowed_rooms(session, principal)
                user = auth.user(session, principal)
                if args.room not in {r.id for r in rooms}:
                    raise DomainError("room_not_approved", "現在の収集権限を確認できません。", 403)
                account_id = user.account_id
            with store.client_for(args.user, account_id) as client:
                output(poll_room(db, client, args.room))
        elif args.command == "drain-webhooks":
            from .sync import drain_webhooks
            output(drain_webhooks(db))
        elif args.command == "index":
            from .embeddings import LocalEmbedder, index_messages
            if not settings.semantic_enabled:
                raise DomainError("semantic_disabled", "SCS_SEMANTIC_ENABLED=trueを明示してください。モデルを取得する場合があります。")
            output(index_messages(db, LocalEmbedder(settings)))
        db.close()
    except DomainError as exc:
        output({"error": exc.code, "message": exc.message})
        raise SystemExit(1) from None
    except Exception:
        output({"error": "configuration_or_runtime", "message": "処理を完了できません。設定・入力形式・接続環境を確認してください。秘密情報を出力せずに終了しました。"})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

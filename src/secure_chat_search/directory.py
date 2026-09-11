"""既存認証基盤と対応する利用者・承認ルームを、管理者CLIから登録する。"""
from .db import User, Room
from .ingest import identifier
from .errors import AppError


def configure(db, document: dict) -> dict:
    """全件検証後に反映。列挙されていない既存項目は変更しない（明示的失効を使う）。"""
    try:
        tenant = document["tenant"]
        if not isinstance(tenant, str) or not 1 <= len(tenant) <= 100:
            raise ValueError("tenant")
        users, rooms = document.get("users", []), document.get("rooms", [])
        if not isinstance(users, list) or not isinstance(rooms, list):
            raise ValueError("lists")
        for u in users:
            if not isinstance(u["subject"], str) or not 1 <= len(u["subject"]) <= 200:
                raise ValueError("subject")
            identifier(u["account_id"])
            if not isinstance(u.get("enabled", True), bool) or not isinstance(u["name"], str) or len(u["name"]) > 200:
                raise ValueError("user")
        for r in rooms:
            identifier(r["room_id"])
            if r.get("kind", "group") not in ("group", "direct", "my") or not isinstance(r.get("enabled", True), bool):
                raise ValueError("room")
            if not isinstance(r["name"], str) or len(r["name"]) > 200:
                raise ValueError("room name")
    except (KeyError, TypeError, ValueError):
        raise AppError(422, "invalid_directory", "利用者・ルーム設定が不正です") from None
    with db.session.begin() as s:
        for u in users:
            row = s.get(User, (tenant, u["subject"]))
            if row is None:
                row = User(tenant=tenant, subject=u["subject"])
                s.add(row)
            row.name, row.account_id, row.enabled = u["name"], str(u["account_id"]), u.get("enabled", True)
        for r in rooms:
            row = s.get(Room, (tenant, str(r["room_id"])))
            if row is None:
                row = Room(tenant=tenant, room_id=str(r["room_id"]))
                s.add(row)
            row.name, row.kind, row.enabled = r["name"], r.get("kind", "group"), r.get("enabled", True)
    return {"tenant": tenant, "users_updated": len(users), "rooms_updated": len(rooms)}

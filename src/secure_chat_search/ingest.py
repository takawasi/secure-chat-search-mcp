"""CSVを原子的に取り込み、APIと共通の更新規則を適用する。"""
from dataclasses import dataclass
import csv
import hashlib
import io
import json
import time
import uuid
from sqlalchemy import select, delete
from .db import Database, Message, Room, ImportRun
from .errors import AppError
from .text import normalized, references, timestamp

HEADERS = {"ルームID": "room_id", "メッセージID": "message_id", "アカウントID": "sender_id",
           "発言者": "sender_name", "送信日時": "sent_at", "更新日時": "updated_at",
           "本文": "body", "状態": "status"}
REQUIRED = {"room_id", "sender_id", "sent_at", "body"}

@dataclass
class Record:
    room_id: str
    remote_id: str | None
    sender_id: str
    sender_name: str
    sent_at: int
    version_at: int
    body: str
    deleted: bool = False

def identifier(value: str) -> str:
    v = str(value)
    if not v.isascii() or not v.isdecimal() or len(v) > 64:
        raise AppError(422, "invalid_id", "ルーム・メッセージ・アカウントIDには数字を指定してください")
    return v

def upsert(s, tenant: str, record: Record, observed: int, source="api", snapshot_id=None, row_number=0):
    r = record
    room = s.get(Room, (tenant, r.room_id))
    if not room or room.kind != "group" or not room.enabled:
        return "excluded"
    if r.remote_id:
        key = f"{tenant}:{r.room_id}:remote:{r.remote_id}"
    else:
        # IDなしはスナップショット内の行を識別する。本文ハッシュをメッセージIDとは扱わない。
        key = f"{tenant}:{r.room_id}:snapshot:{snapshot_id}:{row_number}"
    mid = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
    existing = s.get(Message, mid)
    if existing:
        # 同じ更新秒で矛盾するイベントは保守的に削除を優先。復元は新しい版だけ。
        if r.version_at < existing.version_at or (existing.deleted and r.version_at <= existing.version_at and not r.deleted):
            return "stale"
        if source == "snapshot" and observed < existing.observed_at and r.version_at <= existing.version_at:
            return "stale"
        body = "" if r.deleted else r.body
        if existing.version_at == r.version_at and existing.body == body and existing.deleted == r.deleted:
            existing.observed_at = max(observed, existing.observed_at)
            return "unchanged"
        target, outcome = existing, "updated"
    else:
        target, outcome = Message(id=mid, tenant=tenant, room_id=r.room_id), "inserted"
        s.add(target)
    target.remote_id, target.sender_id, target.sender_name = r.remote_id, r.sender_id, r.sender_name
    target.sent_at, target.version_at, target.observed_at = r.sent_at, r.version_at, observed
    target.deleted, target.body = r.deleted, "" if r.deleted else r.body
    target.normalized = normalized(target.body)
    target.references = references(target.body)
    target.source, target.snapshot_id = source, snapshot_id
    s.flush()
    return outcome

class Importer:
    def __init__(self, db: Database):
        self.db = db

    def import_csv(self, data: bytes, tenant: str, snapshot_at: int,
                   encoding="utf-8-sig", replace_rooms: list[str] | None = None) -> dict:
        snapshot_at = timestamp(snapshot_at)
        if len(data) > 2 * 1024 * 1024:
            raise AppError(413, "csv_too_large", "CSVは2MiB以下に分割してください")
        if encoding not in ("utf-8-sig", "cp932"):
            raise AppError(422, "invalid_encoding", "文字コードはutf-8-sig / cp932です")
        try:
            text = data.decode(encoding)
        except UnicodeError:
            raise AppError(422, "invalid_encoding", "指定文字コードでCSVを読めません") from None
        reader = csv.DictReader(io.StringIO(text, newline=""))
        headers = reader.fieldnames or []
        mapped = [HEADERS.get(h, h) for h in headers]
        if len(set(mapped)) != len(mapped) or not REQUIRED.issubset(mapped):
            raise AppError(422, "invalid_csv_headers", "CSVの必須列不足または列名重複です。examples/history.csvを参照してください")
        records = []
        try:
            for index, raw in enumerate(reader, 1):
                if index > 10000:
                    raise AppError(413, "too_many_rows", "CSVは1万行以下に分割してください")
                if None in raw or any(v is None for v in raw.values()):
                    raise AppError(422, "invalid_csv_row", f"CSV {index + 1}行目の列数が不正です")
                row = {HEADERS.get(k, k): v for k, v in raw.items()}
                status = row.get("status", "active") or "active"
                if status not in ("active", "deleted", "current", "historical"):
                    raise AppError(422, "invalid_status", f"CSV {index + 1}行目の状態が不正です")
                # 編集前の本文は検索対象にしない。
                if status == "historical":
                    continue
                sent = timestamp(row["sent_at"])
                version = timestamp(row["updated_at"]) if row.get("updated_at") else (snapshot_at if status == "deleted" else sent)
                if sent > snapshot_at or version > snapshot_at or version < sent:
                    raise AppError(422, "snapshot_time_mismatch", "CSVの送信・更新日時とスナップショット時点が矛盾しています")
                body = row["body"]
                if len(body) > 20000 or "\x00" in body or len(row.get("sender_name", "")) > 200:
                    raise AppError(422, "invalid_body", "本文・発言者名が長すぎるか、禁止文字を含んでいます")
                records.append((index, Record(identifier(row["room_id"]),
                    identifier(row["message_id"]) if row.get("message_id") else None,
                    identifier(row["sender_id"]), row.get("sender_name", ""), sent, version,
                    body, status == "deleted")))
        except csv.Error:
            raise AppError(422, "invalid_csv", "CSVを解析できません") from None
        replace_rooms = sorted(set(identifier(x) for x in (replace_rooms or [])))
        digest = hashlib.sha256(tenant.encode() + str(snapshot_at).encode() + data +
                                json.dumps(replace_rooms).encode()).hexdigest()
        report = {k: 0 for k in ("inserted", "updated", "unchanged", "stale", "excluded")}
        report.update({"snapshot_id": digest, "snapshot_at": snapshot_at, "rows": len(records),
                       "idless_rows": sum(r.remote_id is None for _, r in records), "replayed": False})
        with self.db.session.begin() as s:
            previous = s.get(ImportRun, digest)
            if previous:
                return {**previous.report, "replayed": True}
            for rid in replace_rooms:
                room = s.get(Room, (tenant, rid))
                if not room or room.kind != "group" or not room.enabled:
                    raise AppError(422, "invalid_replace_scope", "置換対象には承認済みグループを指定してください")
                if snapshot_at < room.snapshot_at:
                    raise AppError(409, "older_snapshot", "古いスナップショットによる置換を拒否しました")
                # 宣言された部屋のIDなし履歴だけを置換。API行や他の部屋は消さない。
                s.execute(delete(Message).where(Message.tenant == tenant, Message.room_id == rid,
                          Message.remote_id.is_(None), Message.source == "snapshot"))
                room.snapshot_at, room.snapshot_id = snapshot_at, digest
            for index, record in records:
                if record.remote_id is None and record.room_id not in replace_rooms:
                    # IDなしの追記蓄積は重複・削除を誤るため、部屋単位の完全スナップショット宣言を必須とする。
                    room = s.get(Room, (tenant, record.room_id))
                    if room and room.kind == "group" and room.enabled:
                        raise AppError(422, "idless_requires_replace", "IDなし履歴にはreplace_roomsで完全スナップショット対象を指定してください")
                result = upsert(s, tenant, record, snapshot_at, "snapshot", digest, index)
                report[result] += 1
            s.add(ImportRun(id=digest, tenant=tenant, at=int(time.time()), report=report))
        return report

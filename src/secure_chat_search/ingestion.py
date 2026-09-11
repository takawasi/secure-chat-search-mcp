"""正規化CSVとAPIを取り込む。実Chatwork CSVの列は実サンプルで確認する。"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from sqlalchemy import select
from .core import DomainError, ImportBatch, Message, Room, numeric_id, timestamp, wipe


@dataclass(frozen=True)
class Incoming:
    room_id: str
    external_id: str | None
    account_id: str
    author: str
    sent_at: int
    version_at: int
    body: str
    deleted: bool = False


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def display_text(body: str) -> str:
    body = re.sub(r"\[rp\s+[^\]]*\]|\[qtmeta\s+[^\]]*\]|\[To:[^\]]*\]", "", body)
    return re.sub(r"\[/?(?:info|title|qt|code|hr|toall)\]", "", body).strip()


def references(body: str) -> list:
    return [{"room_id": room, "message_id": message}
            for room, message in re.findall(r"\[rp\s+[^\]]*to=([0-9]+)-([0-9]+)[^\]]*\]", body)]


def upsert(session, item: Incoming, observed_at: int, source: str, internal_id: str | None = None):
    key = internal_id or f"cw:{item.room_id}:{item.external_id}"
    existing = session.scalar(select(Message).where(Message.id == key).with_for_update())
    if existing:
        if (existing.room_id, existing.account_id, existing.sent_at) != (item.room_id, item.account_id, item.sent_at):
            raise DomainError("identity_conflict", "同じIDの発言属性が一致しません。取り込みを中止しました。")
        if (item.version_at, observed_at) < (existing.version_at, existing.observed_at):
            return False
        if existing.deleted and not item.deleted and item.version_at <= existing.version_at:
            return False
    else:
        existing = Message(id=key, room_id=item.room_id, external_id=item.external_id,
                           account_id=item.account_id, sent_at=item.sent_at, body="", normalized="",
                           author=item.author, version_at=item.version_at, observed_at=observed_at,
                           source=source, refs=[], deleted=False)
        session.add(existing)
    text = display_text(item.body)
    if existing.body != text:
        existing.embedding = existing.embedding_model = None
    existing.author = item.author
    existing.body, existing.normalized = text, normalize(text)
    existing.version_at, existing.observed_at, existing.source = item.version_at, observed_at, source
    existing.refs, existing.deleted = references(item.body), item.deleted
    if item.deleted:
        wipe(existing)
    return True


def parse_csv(raw: bytes, manifest: dict):
    if len(raw) > 20_000_000:
        raise DomainError("export_too_large", "このPoCのCSV上限は20MBです。範囲を分割してください。")
    try:
        if manifest.get("complete_snapshot") is not True:
            raise DomainError("incomplete_snapshot", "対象期間・ルームの完全スナップショットであることを指定してください。")
        snapshot = timestamp(manifest["snapshot_at"])
        start, end = timestamp(manifest["coverage_from"]), timestamp(manifest["coverage_through"])
        scope = {numeric_id(x) for x in manifest["room_ids"]}
        if not scope or not start <= end <= snapshot:
            raise ValueError()
        columns = manifest.get("columns", {})
        required = ["room_id", "account_id", "sent_at", "body"]
        content = raw.decode(manifest.get("encoding", "utf-8-sig"))
        reader = csv.DictReader(io.StringIO(content, newline=""))
        if (not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or
                any(columns.get(key, key) not in reader.fieldnames for key in required)):
            raise DomainError("csv_columns", "CSVの必須列・重複列を確認してください。")
        items = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise DomainError("csv_row", "CSVの列数が一致しません。")
            def get(key, default=""):
                return row.get(columns.get(key, key), default)
            room = numeric_id(get("room_id"))
            account = numeric_id(get("account_id"))
            sent = timestamp(get("sent_at"))
            version = timestamp(get("updated_at") or sent)
            if not start <= sent <= end or not sent <= version <= snapshot:
                raise DomainError("csv_time", "CSVの日時が宣言された範囲と一致しません。")
            body = get("body")
            if len(body) > 100000 or len(get("author")) > 200:
                raise DomainError("csv_length", "本文または発言者名が上限を超えています。")
            flag = get("deleted", "false").strip().lower()
            if flag not in {"", "false", "true", "0", "1"}:
                raise DomainError("csv_deleted", "deleted列はtrue/falseまたは1/0で指定してください。")
            deleted = flag in {"true", "1"}
            if "deleted_marker" in manifest and body == manifest["deleted_marker"]:
                deleted = True
            external = numeric_id(get("message_id")) if get("message_id") else None
            items.append(Incoming(room, external, account, get("author") or account, sent, version, body, deleted))
        return items, scope, snapshot, start, end
    except DomainError:
        raise
    except (KeyError, ValueError, TypeError, UnicodeError, LookupError, csv.Error):
        raise DomainError("invalid_export", "CSVとマニフェストの形式を確認してください。") from None


def signature(item):
    return item.room_id, item.account_id, item.sent_at, display_text(item.body)


def import_csv(db, raw: bytes, manifest: dict):
    items, scope, snapshot, start, end = parse_csv(raw, manifest)
    with db.session() as session:
        rooms = list(session.scalars(select(Room).where(Room.id.in_(scope), Room.approved.is_(True),
                                                        Room.kind == "group").order_by(Room.id).with_for_update()))
        allowed = {room.id for room in rooms}
        # 許可対象が増えた後の再取り込みは別バッチとする。
        fingerprint = raw + json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode() + json.dumps(sorted(allowed)).encode()
        digest = hashlib.sha256(fingerprint).hexdigest()
        if session.get(ImportBatch, digest):
            return {"imported": 0, "matched": 0, "excluded": 0, "replayed": True}
        if any(room.snapshot_at is not None and snapshot < room.snapshot_at for room in rooms):
            raise DomainError("stale_snapshot", "より新しいスナップショットがすでにあります。")
        for message in session.scalars(select(Message).where(Message.room_id.in_(allowed),
                Message.external_id.is_(None), Message.sent_at.between(start, end),
                Message.observed_at <= snapshot)):
            wipe(message)
        candidates = [x for x in items if x.room_id in allowed]
        counts = Counter(signature(x) for x in candidates if not x.deleted)
        imported = matched = 0
        for index, item in enumerate(items):
            if item.room_id not in allowed:
                continue
            internal = None
            if item.external_id is None:
                text = display_text(item.body)
                live = list(session.scalars(select(Message).where(Message.room_id == item.room_id,
                    Message.external_id.is_not(None), Message.account_id == item.account_id,
                    Message.sent_at == item.sent_at, Message.body == text, Message.deleted.is_(False))))
                if not item.deleted and counts[signature(item)] == 1 and len(live) == 1:
                    matched += 1
                    continue
                internal = f"export:{item.room_id}:{digest[:24]}:{index}"
            imported += int(upsert(session, item, snapshot, "export", internal))
        for room in rooms:
            room.snapshot_at, room.coverage_from, room.coverage_through = snapshot, start, end
            if (room.gap_suspected and room.gap_since is not None and room.last_poll_at is not None and
                    start <= room.gap_since and end >= room.last_poll_at):
                room.gap_suspected, room.gap_since = False, None
        session.add(ImportBatch(digest=digest, snapshot_at=snapshot, rows=imported))
        return {"imported": imported, "matched": matched, "excluded": len(items) - len(candidates), "replayed": False}

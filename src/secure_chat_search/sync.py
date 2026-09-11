"""重複取得・更新順序・欠損疑いと、永続化したWebhookの処理。"""
import hashlib
import json
import time
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .auth import Principal, Policy
from .db import Database, Room, Message, SyncState, Inbox
from .errors import AppError, UpstreamError
from .chatwork import record_from_api, verify_signature
from .ingest import upsert, identifier

class Synchronizer:
    def __init__(self, db: Database):
        self.db = db

    def apply_recent(self, tenant: str, room_id: str, rows: list, observed_at: int | None = None) -> dict:
        now = int(time.time()) if observed_at is None else observed_at
        if len(rows) > 100:
            raise AppError(422, "too_many_recent", "直近取得は100件以下です")
        records = [record_from_api(room_id, row) for row in rows]
        report = {k: 0 for k in ("inserted", "updated", "unchanged", "stale", "excluded")}
        with self.db.session.begin() as s:
            room = s.get(Room, (tenant, room_id))
            if not room or not room.enabled or room.kind != "group":
                raise AppError(403, "room_not_approved", "収集対象として承認されたグループではありません")
            state = s.get(SyncState, (tenant, room_id))
            if not state:
                state = SyncState(tenant=tenant, room_id=room_id, last_success=0,
                                  last_count=0, gap_suspected=False, error_code="")
                s.add(state)
            ids = [r.remote_id for r in records]
            overlap = s.scalar(select(Message.id).where(Message.tenant == tenant,
                             Message.room_id == room_id, Message.remote_id.in_(ids)).limit(1)) if ids else None
            # 100件がすべて未知なら、100件のさらに前を取りこぼしていないと証明できない。
            if len(rows) == 100 and not overlap:
                state.gap_suspected = True
            for record in records:
                report[upsert(s, tenant, record, now)] += 1
            state.last_success = max(state.last_success, now)
            state.last_count = len(rows)
            state.error_code = ""
            report.update({"gap_suspected": state.gap_suspected, "received": len(rows), "last_success": state.last_success})
        return report

    def poll(self, p: Principal, room_id: str, client, policy: Policy):
        if room_id not in policy.rooms(p):
            raise AppError(403, "room_not_allowed", "収集する部屋の閲覧権限がありません")
        try:
            return self.apply_recent(p.tenant, room_id, client.recent(p, room_id))
        except AppError as e:
            with self.db.session.begin() as s:
                state = s.get(SyncState, (p.tenant, room_id))
                if not state:
                    state = SyncState(tenant=p.tenant, room_id=room_id)
                    s.add(state)
                state.error_code = e.code
            raise

class WebhookInbox:
    def __init__(self, db: Database, token: str, tenant: str):
        self.db, self.token, self.tenant = db, token, tenant

    def accept(self, raw: bytes, signature: str) -> dict:
        verify_signature(raw, signature, self.token)
        try:
            value = json.loads(raw)
            event_type = value["webhook_event_type"]
            event = value["webhook_event"]
            room_id = identifier(event["room_id"])
            record_from_api(room_id, event)
        except (ValueError, KeyError, TypeError, AppError):
            raise AppError(400, "invalid_webhook_body", "Webhook本文が不正です") from None
        if event_type not in ("message_created", "message_updated", "mention_to_me"):
            return {"accepted": False, "reason": "unsupported_event"}
        digest = hashlib.sha256(self.tenant.encode() + raw).hexdigest()
        try:
            with self.db.session.begin() as s:
                room = s.get(Room, (self.tenant, room_id))
                if not room or room.kind != "group" or not room.enabled:
                    return {"accepted": False, "reason": "room_not_approved"}
                if s.get(Inbox, digest):
                    return {"accepted": True, "duplicate": True}
                s.add(Inbox(id=digest, tenant=self.tenant, received_at=int(time.time()), payload=value))
        except IntegrityError:
            return {"accepted": True, "duplicate": True}
        return {"accepted": True, "duplicate": False}

    def drain(self, limit=100) -> dict:
        done, failed = 0, 0
        with self.db.session() as s:
            ids = list(s.scalars(select(Inbox.id).where(Inbox.tenant == self.tenant,
                       Inbox.state.in_(["pending", "retry"]), Inbox.attempts < 5)
                       .order_by(Inbox.received_at, Inbox.id).limit(limit)))
        # 参照実装はworkerを1つで実行する。複数worker化はロック/lease付きキューへの変更が必要。
        for event_id in ids:
            try:
                with self.db.session.begin() as s:
                    item = s.get(Inbox, event_id)
                    if item.state == "processed":
                        continue
                    event = item.payload["webhook_event"]
                    record = record_from_api(str(event["room_id"]), event)
                    upsert(s, self.tenant, record, item.received_at, "webhook")
                    item.attempts += 1
                    item.state, item.payload, item.error_code = "processed", {}, ""
                done += 1
            except Exception:
                with self.db.session.begin() as s:
                    item = s.get(Inbox, event_id)
                    item.attempts += 1
                    item.state = "failed" if item.attempts >= 5 else "retry"
                    item.error_code = "webhook_processing_failed"
                failed += 1
        return {"processed": done, "failed": failed}

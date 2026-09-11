"""API同期とWebhook受信箱。通知の完全配送や削除の即時反映は保証しない。"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import time
from collections import Counter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .core import DomainError, Message, Room, WebhookEvent, numeric_id, timestamp, wipe
from .ingestion import Incoming, display_text, signature, upsert


def parse_api(room_id: str, payload: dict) -> Incoming:
    try:
        account = payload.get("account") or {"account_id": payload["account_id"]}
        sent = timestamp(payload["send_time"])
        version = timestamp(payload.get("update_time") or sent)
        body = payload["body"]
        author = str(account.get("name") or account["account_id"])
        if version < sent or not isinstance(body, str) or len(body) > 100000 or len(author) > 200:
            raise ValueError()
        return Incoming(numeric_id(room_id), numeric_id(payload["message_id"]),
                        numeric_id(account["account_id"]), author, sent, version, body)
    except DomainError:
        raise
    except (KeyError, TypeError, ValueError):
        raise DomainError("upstream_format", "メッセージの形式を確認できません。", 503) from None


def apply_messages(db, room_id, payloads: list, observed_at=None, source="api", polling=True):
    room_id = numeric_id(room_id)
    observed = timestamp(observed_at if observed_at is not None else int(time.time()))
    if not isinstance(payloads, list) or (polling and len(payloads) > 100):
        raise DomainError("upstream_format", "一覧の件数が上限を超えています。", 503)
    items = [parse_api(room_id, payload) for payload in payloads]
    if any(item.version_at > observed for item in items):
        raise DomainError("future_message", "取得時点より新しいメッセージは反映できません。")
    with db.session() as session:
        room = session.scalar(select(Room).where(Room.id == room_id).with_for_update())
        if room is None or not room.approved or room.kind != "group":
            raise DomainError("room_not_approved", "このルームは収集対象ではありません。", 403)
        keys = {f"cw:{room_id}:{x.external_id}" for x in items}
        known = set(session.scalars(select(Message.id).where(Message.id.in_(keys)))) if keys else set()
        if polling and len(items) == 100 and not known:
            room.gap_suspected = True
            if room.gap_since is None:
                room.gap_since = room.last_poll_at or room.coverage_through or min(x.sent_at for x in items)
        counts = Counter(signature(x) for x in items)
        changed = 0
        for item in items:
            changed += int(upsert(session, item, observed, source))
            if counts[signature(item)] == 1:
                old = list(session.scalars(select(Message).where(Message.room_id == room_id,
                    Message.external_id.is_(None), Message.account_id == item.account_id,
                    Message.sent_at == item.sent_at, Message.body == display_text(item.body),
                    Message.deleted.is_(False))))
                if len(old) == 1:
                    wipe(old[0])
        if polling:
            room.last_poll_at, room.sync_error = observed, None
        return {"changed": changed, "received": len(items), "gap_suspected": room.gap_suspected}


def poll_room(db, client, room_id):
    try:
        return apply_messages(db, room_id, client.messages(room_id))
    except DomainError as exc:
        with db.session() as session:
            room = session.get(Room, str(room_id))
            if room:
                room.sync_error = exc.code
        raise


def receive_webhook(db, raw: bytes, signature_header: str, token: str):
    try:
        secret = base64.b64decode(token, validate=True)
        if not secret:
            raise ValueError()
        expected = base64.b64encode(hmac.new(secret, raw, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(expected, signature_header):
            raise ValueError()
    except (ValueError, TypeError):
        raise DomainError("webhook_signature", "Webhook署名を確認できません。", 401) from None
    try:
        payload = json.loads(raw)
        if payload["webhook_event_type"] not in {"message_created", "message_updated"}:
            raise DomainError("webhook_event", "このイベントは取り込み対象ではありません。")
        item = payload["webhook_event"]
        room_id = numeric_id(item["room_id"])
        parsed = parse_api(room_id, item)
        observed = timestamp(payload["webhook_event_time"])
        if parsed.version_at > observed:
            raise ValueError()
    except DomainError:
        raise
    except (KeyError, TypeError, ValueError):
        raise DomainError("webhook_format", "Webhookの形式を確認できません。") from None
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with db.session() as session:
            room = session.get(Room, room_id)
            if room is None or not room.approved or room.kind != "group":
                raise DomainError("room_not_approved", "このルームは収集対象ではありません。", 403)
            if session.get(WebhookEvent, digest):
                return {"accepted": True, "duplicate": True}
            session.add(WebhookEvent(digest=digest, payload=payload, received_at=int(time.time()), done=False))
    except IntegrityError:
        # 同一ダイジェストの並列配送は同じ受理済みイベントと扱う。
        with db.session() as session:
            if not session.get(WebhookEvent, digest):
                raise
        return {"accepted": True, "duplicate": True}
    return {"accepted": True, "duplicate": False}


def drain_webhooks(db, limit=100):
    """PoCは単一ワーカー。処理済み本文は消し、配送重複判定用のハッシュを残す。"""
    with db.session() as session:
        pending = list(session.scalars(select(WebhookEvent).where(WebhookEvent.done.is_(False))
                                      .order_by(WebhookEvent.received_at, WebhookEvent.digest).limit(limit)))
    succeeded = failed = 0
    for entry in pending:
        error = None
        try:
            payload = entry.payload
            event = payload["webhook_event"]
            apply_messages(db, event["room_id"], [event], payload["webhook_event_time"], "webhook", False)
            succeeded += 1
        except DomainError as exc:
            failed += 1
            error = exc.code
        with db.session() as session:
            current = session.get(WebhookEvent, entry.digest)
            current.done, current.error, current.payload = True, error, {}
    return {"succeeded": succeeded, "failed": failed}

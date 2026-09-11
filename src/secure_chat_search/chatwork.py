"""実Chatwork APIの境界。認可失敗時の代替アカウントへの切替は行わない。"""
import base64
import binascii
import hashlib
import hmac
import httpx
from .auth import Principal
from .errors import AppError, UpstreamError
from .ingest import identifier, Record
from .text import timestamp

class ChatworkClient:
    def __init__(self, token_provider, transport=None):
        self.tokens, self.transport = token_provider, transport

    def _get(self, p: Principal, path: str, params=None):
        token = self.tokens.get_access_token(p)
        try:
            # 固定の公式API宛てのみ。リダイレクトに秘密を転送しない。
            with httpx.Client(base_url="https://api.chatwork.com/v2/", timeout=8,
                              follow_redirects=False, transport=self.transport) as client:
                response = client.get(path, params=params, headers={"Authorization": "Bearer " + token})
            if response.status_code == 204:
                return []
            if response.status_code == 429:
                raise UpstreamError("chatwork_rate_limited")
            if response.status_code in (401, 403):
                raise UpstreamError("chatwork_access_revoked")
            if response.status_code != 200:
                raise UpstreamError("chatwork_unavailable")
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise UpstreamError("chatwork_invalid_response") from None

    def current_group_rooms(self, p: Principal, account_id: str) -> set[str]:
        me = self._get(p, "me")
        if not isinstance(me, dict) or str(me.get("account_id")) != account_id:
            raise UpstreamError("chatwork_account_mismatch")
        rooms = self._get(p, "rooms")
        if not isinstance(rooms, list):
            raise UpstreamError("chatwork_invalid_rooms")
        try:
            return {identifier(r["room_id"]) for r in rooms if r["type"] == "group"}
        except (KeyError, TypeError, AppError):
            raise UpstreamError("chatwork_invalid_rooms") from None

    def recent(self, p: Principal, room_id: str) -> list:
        rows = self._get(p, f"rooms/{identifier(room_id)}/messages", {"force": 1})
        if not isinstance(rows, list) or len(rows) > 100:
            raise UpstreamError("chatwork_invalid_messages")
        return rows

    def message(self, p: Principal, room_id: str, message_id: str):
        return self._get(p, f"rooms/{identifier(room_id)}/messages/{identifier(message_id)}")


def record_from_api(room_id: str, value: dict) -> Record:
    try:
        if not isinstance(value, dict):
            raise ValueError("message object required")
        account = value.get("account", {})
        if not isinstance(account, dict):
            raise ValueError("account object required")
        sid = account.get("account_id", value.get("account_id", value.get("from_account_id")))
        sent = timestamp(value["send_time"])
        updated = timestamp(value.get("update_time") or sent)
        body = value["body"]
        if not isinstance(body, str) or len(body) > 20000 or "\x00" in body or updated < sent:
            raise ValueError("invalid body / time")
        return Record(identifier(room_id), identifier(value["message_id"]), identifier(sid),
                      str(account.get("name", ""))[:200], sent, updated, body)
    except (KeyError, TypeError, ValueError, AppError):
        raise UpstreamError("chatwork_invalid_message") from None


def verify_signature(raw: bytes, signature: str, token: str) -> None:
    if not token:
        raise AppError(503, "webhook_not_configured", "Webhookが未設定です")
    try:
        secret = base64.b64decode(token, validate=True)
        supplied = base64.b64decode(signature, validate=True)
        if len(secret) < 16 or not hmac.compare_digest(hmac.new(secret, raw, hashlib.sha256).digest(), supplied):
            raise ValueError("invalid signature")
    except (ValueError, binascii.Error):
        raise AppError(401, "invalid_webhook_signature", "Webhook署名が無効です") from None

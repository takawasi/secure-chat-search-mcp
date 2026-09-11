"""実APIアダプター。通信先を固定し、エラー本文・トークンを返さない。"""
from __future__ import annotations
import json
import time
from pathlib import Path
import httpx
from .core import DomainError, numeric_id


class ChatworkClient:
    def __init__(self, token: str, transport=None):
        if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
            raise DomainError("auth_required", "Chatwork認証情報を確認できません。", 503)
        self.http = httpx.Client(base_url="https://api.chatwork.com/v2/",
                                 headers={"Authorization": "Bearer " + token},
                                 timeout=10, follow_redirects=False, transport=transport)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def get(self, path, params=None):
        try:
            response = self.http.get(path, params=params)
        except httpx.HTTPError:
            raise DomainError("upstream_unavailable", "Chatworkに接続できません。", 503) from None
        if response.status_code == 204:
            return []
        if response.status_code in {401, 403}:
            raise DomainError("auth_required", "Chatworkの再認証または権限確認が必要です。", 503)
        if response.status_code == 429:
            raise DomainError("rate_limited", "Chatwork APIの利用制限に達しました。", 503)
        if response.status_code != 200:
            raise DomainError("upstream_unavailable", "Chatworkの応答を確認できません。", 503)
        try:
            return response.json()
        except ValueError:
            raise DomainError("upstream_format", "Chatworkの応答形式を確認できません。", 503) from None

    def me(self):
        data = self.get("me")
        if not isinstance(data, dict) or "account_id" not in data:
            raise DomainError("upstream_format", "Chatworkのアカウント情報を確認できません。", 503)
        return data

    def rooms(self):
        data = self.get("rooms")
        if not isinstance(data, list) or any(not isinstance(r, dict) or "room_id" not in r for r in data):
            raise DomainError("upstream_format", "Chatworkのルーム情報を確認できません。", 503)
        return data

    def messages(self, room_id):
        data = self.get(f"rooms/{numeric_id(room_id)}/messages", {"force": "1"})
        if not isinstance(data, list) or len(data) > 100:
            raise DomainError("upstream_format", "Chatworkのメッセージ一覧を確認できません。", 503)
        return data

    def message(self, room_id, message_id):
        return self.get(f"rooms/{numeric_id(room_id)}/messages/{numeric_id(message_id)}")


class CredentialStore:
    """既存PoCが更新するマウント済みJSONを読む。OAuth更新や秘密の作成は行わない。"""
    def __init__(self, path: str):
        self.path = Path(path)

    def client_for(self, sub: str, account_id: str):
        try:
            entry = json.loads(self.path.read_text(encoding="utf-8"))[sub]
            if str(entry["account_id"]) != account_id or int(entry["expires_at"]) <= time.time():
                raise ValueError()
            return ChatworkClient(entry["access_token"])
        except (OSError, ValueError, KeyError, TypeError):
            raise DomainError("auth_required", "有効なChatwork認証情報を取得できません。", 503) from None

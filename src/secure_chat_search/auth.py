"""認証と認可。検索・本文取得・期間取得の全経路で再評価する。"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from sqlalchemy import select

from .core import DomainError, Membership, Principal, Room, Settings, User


class MembershipProvider(Protocol):
    def current_rooms(self, session, user: User) -> set[str]: ...


class DemoMemberships:
    def current_rooms(self, session, user):
        return set(session.scalars(select(Membership.room_id).where(Membership.sub == user.sub)))


class LiveMemberships:
    def __init__(self, store):
        self.store = store

    def current_rooms(self, session, user):
        with self.store.client_for(user.sub, user.account_id) as client:
            if str(client.me().get("account_id")) != user.account_id:
                raise DomainError("account_mismatch", "Chatworkアカウントの対応を確認できません。", 503)
            return {str(room["room_id"]) for room in client.rooms() if room.get("type") == "group"}


class Auth:
    def __init__(self, settings: Settings, provider: MembershipProvider):
        self.settings, self.provider = settings, provider
        self.key = None
        if settings.mode == "gateway":
            try:
                self.key = load_pem_public_key(Path(settings.jwt_public_key_file).read_bytes())
                if not isinstance(self.key, RSAPublicKey) or self.key.key_size < 2048:
                    raise ValueError()
            except (ValueError, TypeError, OSError):
                raise DomainError("jwt_key", "2048bit以上のRSA公開鍵を設定してください。") from None

    def authenticate(self, authorization: str | None, cookie: str | None = None) -> Principal:
        token = None
        if authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.lower() != "bearer" or not value or " " in value:
                raise DomainError("unauthenticated", "認証情報を確認できません。", 401)
            token = value
        if self.settings.mode == "demo":
            if token is None and cookie:
                token = "demo:" + cookie
            if token not in {"demo:alice", "demo:bob", "demo:disabled"}:
                raise DomainError("unauthenticated", "デモ利用者を選択してください。", 401)
            return Principal(token.split(":", 1)[1])
        if not token:
            raise DomainError("unauthenticated", "認証情報が必要です。", 401)
        try:
            claims = jwt.decode(token, self.key, algorithms=["RS256"],
                                issuer=self.settings.jwt_issuer, audience=self.settings.jwt_audience,
                                options={"require": ["exp", "iat", "nbf", "sub", "aud", "iss"]})
            if (not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 160 or
                    not 0 < claims["exp"] - claims["iat"] <= 300):
                raise ValueError()
            return Principal(claims["sub"])
        except (jwt.PyJWTError, ValueError, TypeError):
            raise DomainError("unauthenticated", "認証情報を確認できません。", 401) from None

    def user(self, session, principal: Principal) -> User:
        user = session.get(User, principal.sub)
        if not user or not user.enabled:
            raise DomainError("user_disabled", "このサービスの利用が許可されていません。", 403)
        return user

    def allowed_rooms(self, session, principal: Principal) -> list[Room]:
        user = self.user(session, principal)
        try:
            current = self.provider.current_rooms(session, user)
        except DomainError:
            raise
        except Exception:
            raise DomainError("permission_unavailable", "現在の閲覧権限を確認できません。再認証または管理者確認が必要です。", 503) from None
        if not current:
            return []
        return list(session.scalars(select(Room).where(Room.id.in_(current), Room.approved.is_(True),
                                                      Room.kind == "group").order_by(Room.id)))

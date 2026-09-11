"""JWTで本人を確定し、検索直前に現在の権限との積集合を取得する。"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Protocol
import jwt
from sqlalchemy import select
from .config import Settings
from .db import Database, User, Room, Membership
from .errors import AppError, UpstreamError

@dataclass(frozen=True)
class Principal:
    tenant: str
    subject: str

class Auth:
    def __init__(self, settings: Settings):
        self.settings = settings

    def issue_demo(self, subject: str) -> str:
        if self.settings.mode != "demo":
            raise AppError(404, "not_found", "見つかりません")
        now = int(time.time())
        return jwt.encode({"sub": subject, "tid": "demo", "iss": self.settings.jwt_issuer,
                           "aud": self.settings.jwt_audience, "iat": now, "nbf": now,
                           "exp": now + 3600}, self.settings.jwt_secret, algorithm="HS256")

    def verify(self, authorization: str) -> Principal:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AppError(401, "authentication_required", "認証が必要です")
        s = self.settings
        try:
            claims = jwt.decode(token, s.jwt_secret if s.mode == "demo" else s.jwt_public_key,
                                algorithms=["HS256"] if s.mode == "demo" else ["RS256"],
                                audience=s.jwt_audience, issuer=s.jwt_issuer,
                                options={"require": ["exp", "iat", "nbf", "sub", "tid", "iss", "aud"]})
            if not all(isinstance(claims[k], str) and 0 < len(claims[k]) <= 200 for k in ("sub", "tid")):
                raise ValueError("claims")
            return Principal(claims["tid"], claims["sub"])
        except (jwt.PyJWTError, ValueError, TypeError):
            raise AppError(401, "invalid_token", "認証情報が無効または期限切れです") from None

class RoomProvider(Protocol):
    def current_group_rooms(self, principal: Principal, account_id: str) -> set[str]: ...

class FileTokenProvider:
    """既存OAuth側が更新する秘密ファイル。更新処理をこちらで再実装しない。"""
    def __init__(self, path: str):
        self.path = Path(path)

    def get_access_token(self, principal: Principal) -> str:
        try:
            if os.name != "nt" and self.path.stat().st_mode & 0o077:
                raise ValueError("秘密ファイルは0600で管理してください")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            item = data[principal.tenant][principal.subject]
            if item["expires_at"] <= time.time() + 15:
                raise ValueError("expired")
            value = item["access_token"]
            if not isinstance(value, str) or not value:
                raise ValueError("empty")
            return value
        except (OSError, KeyError, TypeError, ValueError):
            raise UpstreamError("chatwork_reauthentication_required") from None

class Policy:
    def __init__(self, db: Database, provider: RoomProvider | None = None):
        self.db, self.provider = db, provider

    def user(self, p: Principal):
        with self.db.session() as s:
            u = s.get(User, (p.tenant, p.subject))
            if not u or not u.enabled:
                raise AppError(403, "user_not_allowed", "このサービスの利用は許可されていません")
            return u

    def rooms(self, p: Principal) -> dict[str, Room]:
        user = self.user(p)
        with self.db.session() as s:
            approved = s.scalars(select(Room).where(Room.tenant == p.tenant,
                                  Room.kind == "group", Room.enabled.is_(True))).all()
            if self.provider:
                try:
                    current = self.provider.current_group_rooms(p, user.account_id)
                except AppError:
                    raise
                except Exception:
                    raise UpstreamError("acl_check_failed") from None
            else:
                current = set(s.scalars(select(Membership.room_id).where(
                    Membership.tenant == p.tenant, Membership.subject == p.subject,
                    Membership.enabled.is_(True))).all())
            return {r.room_id: r for r in approved if r.room_id in current}

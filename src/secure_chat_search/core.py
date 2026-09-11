"""設定、DB、共通型。デモと実データの混在を起動時に拒否する。"""
from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import (BigInteger, Boolean, ForeignKey, Index, Integer, JSON, String, Text,
                        create_engine, event, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def not_found() -> DomainError:
    return DomainError("not_found", "対象が存在しないか、現在の閲覧権限がありません。", 404)


@dataclass(frozen=True)
class Principal:
    sub: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCS_", env_file=".env", extra="ignore")
    mode: str = "demo"
    database_url: str = "sqlite:///.data/demo.sqlite3"
    public_url: str = "http://127.0.0.1:8000"
    allowed_hosts: str = "127.0.0.1,localhost,testserver"
    jwt_issuer: str = ""
    jwt_audience: str = ""
    jwt_public_key_file: str = ""
    credentials_file: str = ""
    webhook_token: str = Field(default="", repr=False)
    semantic_enabled: bool = False
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    model_cache: str = ".data/models"
    semantic_limit: int = Field(default=5000, ge=1, le=100000)
    max_request_bytes: int = Field(default=1048576, ge=1024)
    max_webhook_bytes: int = Field(default=262144, ge=1024)

    @model_validator(mode="after")
    def validate_configuration(self):
        import os
        if self.mode not in {"demo", "gateway"}:
            raise ValueError("SCS_MODE は demo または gateway です。")
        url = urlsplit(self.public_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or
                url.password or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("SCS_PUBLIC_URL はパス・認証情報を含まない HTTP(S) origin を指定してください。")
        if any("*" in x or not x.strip() for x in self.allowed_hosts.split(",")):
            raise ValueError("許可ホストにワイルドカードや空欄は使用できません。")
        if self.mode == "demo" and os.environ.get("K_SERVICE"):
            raise ValueError("Cloud Run でデモ認証を起動できません。gateway を構成してください。")
        if self.mode == "gateway":
            for name in ("jwt_issuer", "jwt_audience", "jwt_public_key_file", "credentials_file"):
                if not getattr(self, name):
                    raise ValueError(f"gateway には {name} が必要です。")
            for name in ("jwt_public_key_file", "credentials_file"):
                if not Path(getattr(self, name)).is_file():
                    raise ValueError(f"{name} のファイルが存在しません。")
            if url.scheme != "https" and url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
                raise ValueError("gateway の公開 origin には HTTPS が必要です。")
        self.public_url = self.public_url.rstrip("/")
        return self


class Base(DeclarativeBase):
    pass


class Meta(Base):
    __tablename__ = "metadata_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class User(Base):
    __tablename__ = "users"
    sub: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    account_id: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="group")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshot_at: Mapped[int | None] = mapped_column(BigInteger)
    coverage_from: Mapped[int | None] = mapped_column(BigInteger)
    coverage_through: Mapped[int | None] = mapped_column(BigInteger)
    last_poll_at: Mapped[int | None] = mapped_column(BigInteger)
    gap_suspected: Mapped[bool] = mapped_column(Boolean, default=False)
    gap_since: Mapped[int | None] = mapped_column(BigInteger)
    sync_error: Mapped[str | None] = mapped_column(String(80))


class Membership(Base):
    """合成データ用。gateway の現在権限はこのテーブルから判断しない。"""
    __tablename__ = "demo_memberships"
    sub: Mapped[str] = mapped_column(ForeignKey("users.sub"), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), primary_key=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    account_id: Mapped[str] = mapped_column(String(32))
    author: Mapped[str] = mapped_column(String(200))
    sent_at: Mapped[int] = mapped_column(BigInteger, index=True)
    version_at: Mapped[int] = mapped_column(BigInteger)
    observed_at: Mapped[int] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    refs: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[list | None] = mapped_column(JSON)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (Index("ix_message_room_time_id", "room_id", "sent_at", "id"),)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_at: Mapped[int] = mapped_column(BigInteger)
    rows: Mapped[int] = mapped_column(Integer)


class WebhookEvent(Base):
    __tablename__ = "webhook_inbox"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[int] = mapped_column(BigInteger)
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    error: Mapped[str | None] = mapped_column(String(80))


class Audit(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(40))
    count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, default=lambda: int(time.time()))


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        kwargs = {}
        if settings.database_url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in settings.database_url:
                kwargs["poolclass"] = StaticPool
            elif settings.database_url.startswith("sqlite:///"):
                Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(settings.database_url, **kwargs)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=30000")
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)
        kind = "synthetic" if self.settings.mode == "demo" else "private"
        with self.session() as session:
            marker = session.get(Meta, "dataset_kind")
            if marker and marker.value != kind:
                raise DomainError("dataset_mode", "デモと実データで同じDBを使用できません。")
            if marker is None:
                if session.scalar(select(User.sub).limit(1)) is not None:
                    raise DomainError("dataset_unmarked", "種別不明の既存DBは起動できません。")
                session.add_all([Meta(key="dataset_kind", value=kind), Meta(key="schema_version", value="1")])
            elif session.get(Meta, "schema_version").value != "1":
                raise DomainError("schema_version", "未対応のDBスキーマです。")

    @contextmanager
    def session(self):
        with self.Session() as session:
            with session.begin():
                yield session

    def audit(self, actor, action, outcome="ok", count=0):
        with self.session() as session:
            session.add(Audit(actor=actor, action=action, outcome=outcome, count=count))

    def close(self):
        self.engine.dispose()


def numeric_id(value) -> str:
    result = str(value)
    if not re.fullmatch(r"[0-9]{1,32}", result):
        raise DomainError("invalid_id", "IDは1〜32桁の数字で指定してください。")
    return result


def timestamp(value) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError()
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            result = int(value)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError()
            result = int(dt.timestamp())
        if not 0 <= result <= 32503680000:
            raise ValueError()
        return result
    except (ValueError, TypeError, OverflowError):
        raise DomainError("invalid_time", "日時はUnix秒か、タイムゾーン付きISO 8601で指定してください。") from None


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def wipe(message: Message):
    message.deleted = True
    message.body = message.normalized = ""
    message.refs = []
    message.embedding = message.embedding_model = None

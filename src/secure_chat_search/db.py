"""共通ストア。SQLiteが標準、PostgreSQLも同じモデルで利用する。"""
from pathlib import Path
from sqlalchemy import (Boolean, Integer, BigInteger, String, Text, JSON, UniqueConstraint,
                        Index, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    tenant: Mapped[str] = mapped_column(String(100), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    account_id: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

class Room(Base):
    __tablename__ = "rooms"
    tenant: Mapped[str] = mapped_column(String(100), primary_key=True)
    room_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="group")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_at: Mapped[int] = mapped_column(BigInteger, default=0)

class Membership(Base):
    __tablename__ = "memberships"
    tenant: Mapped[str] = mapped_column(String(100), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200), primary_key=True)
    room_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(100), index=True)
    room_id: Mapped[str] = mapped_column(String(64), index=True)
    remote_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sender_id: Mapped[str] = mapped_column(String(64))
    sender_name: Mapped[str] = mapped_column(String(200))
    sent_at: Mapped[int] = mapped_column(BigInteger, index=True)
    version_at: Mapped[int] = mapped_column(BigInteger)
    observed_at: Mapped[int] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    references: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (
        UniqueConstraint("tenant", "room_id", "remote_id", name="uq_remote_message"),
        Index("ix_message_scope_time", "tenant", "room_id", "sent_at", "id"),
    )

class SyncState(Base):
    __tablename__ = "sync_states"
    tenant: Mapped[str] = mapped_column(String(100), primary_key=True)
    room_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_success: Mapped[int] = mapped_column(BigInteger, default=0)
    last_count: Mapped[int] = mapped_column(Integer, default=0)
    gap_suspected: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str] = mapped_column(String(100), default="")

class ImportRun(Base):
    __tablename__ = "import_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(100), index=True)
    at: Mapped[int] = mapped_column(BigInteger)
    report: Mapped[dict] = mapped_column(JSON)

class Inbox(Base):
    __tablename__ = "webhook_inbox"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(100), index=True)
    received_at: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(100), default="")

class Audit(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant: Mapped[str] = mapped_column(String(100), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(64))
    at: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32))
    count: Mapped[int] = mapped_column(Integer, default=0)
    # 本文・検索語・アクセストークンは監査ログに保存しない。

class Database:
    def __init__(self, url: str):
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
            elif url.startswith("sqlite:///"):
                Path(url.split("sqlite:///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def pragmas(conn, _):
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=15000")
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)

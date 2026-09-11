"""日本語UI / REST / MCP。すべての本文取得に共通ACLを適用する。"""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import Settings
from .db import Database, User, Room, Membership, SyncState
from .auth import Auth, Principal, Policy, FileTokenProvider
from .chatwork import ChatworkClient
from .search import SearchService
from .embeddings import HttpEmbedder
from .sync import WebhookInbox
from .errors import AppError
from .demo import seed, PEOPLE, new_message, history_csv
from .ingest import Importer
from .text import timestamp
from . import mcp, __version__

WEB = Path(__file__).parent / "web"

class SecurityMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        origin = headers.get("origin")
        allowed = {self.settings.base_url}
        if self.settings.mode == "demo":
            base = urlparse(self.settings.base_url)
            allowed |= {f"http://localhost:{base.port or 8000}", f"http://127.0.0.1:{base.port or 8000}"}
        if origin is not None and origin not in allowed:
            return await JSONResponse({"error": {"code": "invalid_origin", "message": "許可されていない送信元です"}}, status_code=403)(scope, receive, send)
        body = bytearray()
        if scope["method"] in ("POST", "PUT", "PATCH"):
            while True:
                part = await receive()
                if part["type"] == "http.disconnect": return
                body.extend(part.get("body", b""))
                if len(body) > self.settings.max_body_bytes:
                    return await JSONResponse({"error": {"code": "request_too_large", "message": "送信データが上限を超えました"}}, status_code=413)(scope, receive, send)
                if not part.get("more_body", False): break
        consumed = False
        async def replay():
            nonlocal consumed
            if not consumed and scope["method"] in ("POST", "PUT", "PATCH"):
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        async def secured_send(message):
            if message["type"] == "http.response.start":
                extra = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                         (b"referrer-policy", b"no-referrer"), (b"cache-control", b"no-store"),
                         (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)
        await self.app(scope, replay, secured_send)

class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=300)
    mode: str = "related"
    room_id: str | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    sender: str | None = None
    limit: int = Field(default=20, ge=1, le=50)

class SubjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str

class MembershipBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room_id: str
    enabled: bool


def create_app(settings: Settings | None = None, db=None, provider=None, embedder=None):
    settings = settings or Settings.from_env()
    settings.validate()
    owns_database = db is None
    db = db or Database(settings.database_url)
    db.initialize()
    if settings.mode == "demo": seed(db)
    if settings.mode == "integration" and provider is None:
        provider = ChatworkClient(FileTokenProvider(settings.token_file))
    auth, policy = Auth(settings), Policy(db, provider)
    if embedder is None and settings.allow_embeddings:
        embedder = HttpEmbedder(settings.embedding_url, settings.embedding_model, settings.embedding_key)
    service = SearchService(db, policy, settings, embedder)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if owns_database:
                db.engine.dispose()

    app = FastAPI(lifespan=lifespan, title="権限付きチャット検索", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.db, app.state.service, app.state.auth = settings, db, service, auth
    app.add_middleware(SecurityMiddleware, settings=settings)
    hosts = list(set(settings.allowed_hosts + (urlparse(settings.base_url).hostname,)))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    def principal(request):
        value = auth.verify(request.headers.get("authorization", ""))
        policy.user(value)
        return value

    def demo_only():
        if settings.mode != "demo":
            raise AppError(404, "not_found", "見つかりません")

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {}
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status, headers=headers)

    @app.get("/healthz")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/config")
    def config():
        return {"mode": settings.mode, "version": __version__, "embedding_enabled": embedder is not None,
                "people": [{"id": sub, "name": name} for sub, name, _ in PEOPLE] if settings.mode == "demo" else []}

    @app.post("/api/demo/session")
    def session(body: SubjectBody):
        demo_only()
        if body.subject not in {p[0] for p in PEOPLE}:
            raise AppError(404, "not_found", "デモ利用者が見つかりません")
        return {"access_token": auth.issue_demo(body.subject), "expires_in": 3600}

    @app.get("/api/me")
    def me(request: Request):
        return service.summary(principal(request))

    @app.post("/api/search")
    def search(body: SearchBody, request: Request):
        return service.search(principal(request), **body.model_dump())

    @app.get("/api/messages/{message_id}")
    def fetch(message_id: str, request: Request):
        return service.fetch(principal(request), message_id)

    @app.get("/api/timeline")
    def timeline(request: Request, room_id: str, start: int, end: int, offset: int = 0, limit: int = 50):
        return service.timeline(principal(request), room_id, start, end, offset, limit)

    @app.api_route("/mcp", methods=["POST", "GET", "DELETE"])
    async def mcp_endpoint(request: Request):
        p = await run_in_threadpool(principal, request)
        return await mcp.handle(request, service, p)

    @app.post("/hooks/chatwork")
    async def webhook(request: Request):
        inbox = WebhookInbox(db, settings.webhook_token, settings.webhook_tenant)
        return await run_in_threadpool(inbox.accept, await request.body(), request.headers.get("x-chatworkwebhooksignature", ""))

    @app.post("/api/demo/new-message")
    def add_message(request: Request):
        demo_only(); principal(request)
        return new_message(db)

    @app.post("/api/demo/import")
    def import_example(request: Request):
        demo_only(); principal(request)
        return Importer(db).import_csv(history_csv(), "demo", timestamp("2026-09-01T04:00:00+09:00"))

    @app.post("/api/demo/membership")
    def membership(body: MembershipBody, request: Request):
        demo_only(); p = principal(request)
        with db.session.begin() as s:
            membership = s.get(Membership, (p.tenant, p.subject, body.room_id))
            if not membership:
                raise AppError(403, "not_demo_membership", "元から所属するデモルームだけを変更できます")
            membership.enabled = body.enabled
        return {"changed": True, "enabled": body.enabled}

    @app.post("/api/demo/gap")
    def gap(request: Request):
        demo_only(); p = principal(request)
        # 実際の100件分は試験で確認。UIは欠損警告の表示デモをする。
        with db.session.begin() as s:
            state = s.get(SyncState, (p.tenant, "100"))
            state.gap_suspected = True
        return {"gap_suspected": True, "notice": "警告表示の模擬操作です。実際にログを削除していません。"}

    @app.post("/api/demo/reset")
    def reset(request: Request):
        demo_only(); principal(request)
        seed(db, reset=True)
        return {"reset": True}

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app

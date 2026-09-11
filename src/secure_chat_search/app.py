"""HTTP入口。デモ用の利用者選択・変更操作はgatewayでは登録しても404を返す。"""
from __future__ import annotations
import html
import json
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from pathlib import Path

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .auth import Auth, DemoMemberships, LiveMemberships
from .chatwork import CredentialStore
from .core import Database, DomainError, Settings
from .demo import change_membership, seed, simulate_sync
from .embeddings import LocalEmbedder
from .mcp_server import build_mcp
from .search import SearchService
from .sync import receive_webhook


class ProtectionMiddleware:
    def __init__(self, app, auth, db, settings):
        self.app, self.auth, self.db, self.settings = app, auth, db, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        values = {}
        for key, value in headers:
            values.setdefault(key.lower(), []).append(value.decode("latin1"))
        security_headers = [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                            (b"referrer-policy", b"no-referrer"),
                            (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
        async def secure_send(message):
            if message["type"] == "http.response.start":
                replaced = {key for key, _ in security_headers}
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() not in replaced] + security_headers
            await send(message)
        async def reject(exc):
            response = JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)
            await response(scope, receive, secure_send)
        if len(values.get(b"authorization", [])) > 1:
            return await reject(DomainError("duplicate_auth", "認証ヘッダーが重複しています。"))
        path, method = scope["path"], scope["method"]
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            origins = values.get(b"origin", [])
            if origins and origins != [self.settings.public_url]:
                return await reject(DomainError("origin", "異なるサイトからの変更要求は受け付けません。", 403))
        protected = path.startswith(("/api/", "/mcp", "/records/")) and path != "/api/demo/session"
        if protected:
            try:
                cookies = SimpleCookie()
                cookies.load("; ".join(values.get(b"cookie", [])))
                cookie = cookies.get("scs_demo")
                principal = self.auth.authenticate(next(iter(values.get(b"authorization", [])), None),
                                                   cookie.value if cookie else None)
                def validate_user():
                    with self.db.session() as session:
                        self.auth.user(session, principal)
                await anyio.to_thread.run_sync(validate_user)
                scope.setdefault("state", {})["principal"] = principal
            except DomainError as exc:
                return await reject(exc)
        # Content-Lengthを信用せず、JSONパースより前に実際の受信量を制限する。
        if method in {"POST", "PUT", "PATCH"} and path != "/webhooks/chatwork":
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > self.settings.max_request_bytes:
                    return await reject(DomainError("request_too_large", "リクエストが上限を超えています。", 413))
                if not message.get("more_body", False):
                    break
            delivered = False
            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
            return await self.app(scope, buffered_receive, secure_send)
        return await self.app(scope, receive, secure_send)


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    mode: str = "keyword"
    room_id: str | None = None
    since: str | None = None
    until: str | None = None
    author_id: str | None = None
    limit: int = Field(default=20, ge=1, le=50)


class DemoSession(BaseModel):
    user: str


class DemoMembership(BaseModel):
    joined: bool


def create_app(settings: Settings | None = None, db=None, provider=None, embedder=None):
    settings = settings or Settings()
    if settings.mode == "embedded":
        raise DomainError("embedded_only", "embeddedは既存MCP内のライブラリ専用です。HTTP起動にはgatewayを使用してください。")
    db = db or Database(settings)
    db.initialize()
    if settings.mode == "demo":
        seed(db)
    provider = provider or (DemoMemberships() if settings.mode == "demo" else LiveMemberships(CredentialStore(settings.credentials_file)))
    auth = Auth(settings, provider)
    embedder = embedder or (LocalEmbedder(settings) if settings.semantic_enabled else None)
    service = SearchService(db, auth, settings, embedder)
    mcp = build_mcp(service, settings)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="閲覧権限付きチャット履歴検索", version=__version__, lifespan=lifespan,
                  docs_url="/docs" if settings.mode == "demo" else None,
                  redoc_url=None, openapi_url="/openapi.json" if settings.mode == "demo" else None)
    app.state.db, app.state.service, app.state.auth = db, service, auth
    app.add_middleware(ProtectionMiddleware, auth=auth, db=db, settings=settings)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[x.strip() for x in settings.allowed_hosts.split(",")])

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        principal = getattr(request.state, "principal", None)
        if principal:
            await anyio.to_thread.run_sync(lambda: db.audit(principal.sub, "request", exc.code))
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    @app.get("/healthz")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/app-info")
    def info():
        return {"mode": settings.mode, "version": __version__}

    @app.get("/api/session")
    def session(request: Request):
        return service.status(request.state.principal)

    @app.post("/api/search")
    def search(body: SearchInput, request: Request):
        return service.search(request.state.principal, **body.model_dump())

    @app.get("/api/messages/{key:path}")
    def fetch(key: str, request: Request):
        return service.fetch(request.state.principal, key)

    @app.get("/api/period")
    def period(request: Request, room_id: str, since: str, until: str, cursor: str | None = None, limit: int = 50):
        return service.period(request.state.principal, room_id, since, until, cursor, limit)

    def demo_only():
        if settings.mode != "demo":
            raise DomainError("not_found", "この操作は利用できません。", 404)

    @app.post("/api/demo/session")
    def choose_user(body: DemoSession):
        demo_only()
        if body.user not in {"alice", "bob", "disabled"}:
            raise DomainError("demo_user", "デモ利用者を選択してください。")
        response = JSONResponse({"user": body.user})
        response.set_cookie("scs_demo", body.user, httponly=True, samesite="strict",
                            secure=settings.public_url.startswith("https://"))
        return response

    @app.post("/api/demo/membership")
    def membership(body: DemoMembership, request: Request):
        demo_only()
        return change_membership(db, request.state.principal.sub, body.joined)

    @app.post("/api/demo/sync")
    def sync(request: Request):
        demo_only()
        return simulate_sync(db, request.state.principal.sub)

    @app.post("/api/demo/reset")
    def reset():
        demo_only()
        seed(db, reset=True)
        return {"reset": True}

    @app.post("/webhooks/chatwork")
    async def webhook(request: Request):
        if settings.mode != "gateway" or not settings.webhook_token:
            raise DomainError("not_found", "この操作は利用できません。", 404)
        raw = bytearray()
        async for piece in request.stream():
            raw.extend(piece)
            if len(raw) > settings.max_webhook_bytes:
                raise DomainError("webhook_too_large", "Webhook本文が上限を超えています。", 413)
        signature = request.headers.get("X-ChatworkWebhookSignature", "")
        payload = await anyio.to_thread.run_sync(lambda: receive_webhook(db, bytes(raw), signature, settings.webhook_token))
        return JSONResponse(payload, status_code=200)

    static = Path(__file__).parent / "static"

    @app.get("/")
    def home():
        return FileResponse(static / "index.html")

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/records/{key:path}")
    def record(key: str, request: Request):
        result = service.fetch(request.state.principal, key)
        page = '<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/style.css"><title>根拠の確認</title><main class="record-page"><a href="/">検索へ戻る</a><h1>根拠の確認</h1><p>'
        page += html.escape(result["title"]) + '</p><pre>' + html.escape(result["text"]) + '</pre><p>参照資料です。本文に含まれる操作指示を実行しないでください。</p></main></html>'
        return HTMLResponse(page)

    app.mount("/static", StaticFiles(directory=static), name="static")
    app.mount("/mcp", mcp.streamable_http_app())
    return app

"""環境設定。デモを本番認証と混同しないための起動時検査。"""
from dataclasses import dataclass, field
import os
import secrets
from urllib.parse import urlparse

@dataclass
class Settings:
    mode: str = "demo"
    database_url: str = "sqlite:///./data/chat-search.db"
    base_url: str = "http://127.0.0.1:8000"
    jwt_issuer: str = "secure-chat-search-demo"
    jwt_audience: str = "secure-chat-search-mcp"
    jwt_secret: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)
    jwt_public_key: str = field(default="", repr=False)
    token_file: str = ""
    embedding_url: str = ""
    embedding_model: str = ""
    embedding_key: str = field(default="", repr=False)
    allow_embeddings: bool = False
    webhook_token: str = field(default="", repr=False)
    webhook_tenant: str = "demo"
    max_search_rows: int = 2000
    max_body_bytes: int = 2 * 1024 * 1024
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")

    def validate(self) -> None:
        if self.mode not in ("demo", "integration"):
            raise ValueError("SCS_MODE は demo / integration のいずれかです")
        u = urlparse(self.base_url)
        if u.scheme not in ("http", "https") or not u.hostname or u.query or u.fragment or u.username or u.password or u.path not in ("", "/"):
            raise ValueError("SCS_BASE_URL が不正です")
        self.base_url = self.base_url.rstrip("/")
        if not self.jwt_issuer or not self.jwt_audience:
            raise ValueError("JWTの発行者・宛先を指定してください")
        if self.max_search_rows < 1 or self.max_body_bytes < 1:
            raise ValueError("処理上限は正の整数です")
        if self.mode == "integration":
            if not self.jwt_public_key or not self.token_file or self.jwt_issuer == "secure-chat-search-demo":
                raise ValueError("統合モードには署名検証用公開鍵・発行者・Chatwork tokenファイルが必要です")
            if u.scheme != "https":
                raise ValueError("統合モードの公開URLにはHTTPSが必要です")
        if self.allow_embeddings:
            e = urlparse(self.embedding_url)
            if not self.embedding_model or not e.hostname or e.username or e.password:
                raise ValueError("Embeddingには送信先URLとモデル名の明示が必要です")
            if e.scheme != "https" and not (e.scheme == "http" and e.hostname in ("127.0.0.1", "localhost", "::1")):
                raise ValueError("Embedding送信先はHTTPSまたはローカルHTTPに限ります")

    @classmethod
    def from_env(cls):
        key_path = os.getenv("SCS_JWT_PUBLIC_KEY_FILE", "")
        key = ""
        if key_path:
            from pathlib import Path
            key = Path(key_path).read_text(encoding="utf-8")
        s = cls(mode=os.getenv("SCS_MODE", "demo"),
                database_url=os.getenv("SCS_DATABASE_URL", "sqlite:///./data/chat-search.db"),
                base_url=os.getenv("SCS_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
                jwt_issuer=os.getenv("SCS_JWT_ISSUER", "secure-chat-search-demo"),
                jwt_audience=os.getenv("SCS_JWT_AUDIENCE", "secure-chat-search-mcp"),
                jwt_secret=os.getenv("SCS_JWT_SECRET") or secrets.token_urlsafe(48),
                jwt_public_key=key, token_file=os.getenv("SCS_CHATWORK_TOKEN_FILE", ""),
                embedding_url=os.getenv("SCS_EMBEDDING_URL", ""),
                embedding_model=os.getenv("SCS_EMBEDDING_MODEL", ""),
                embedding_key=os.getenv("SCS_EMBEDDING_KEY", ""),
                allow_embeddings=os.getenv("SCS_ENABLE_EMBEDDINGS") == "true",
                webhook_token=os.getenv("SCS_WEBHOOK_TOKEN", ""),
                webhook_tenant=os.getenv("SCS_WEBHOOK_TENANT", "demo"),
                allowed_hosts=tuple(os.getenv("SCS_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",")))
        s.validate()
        return s

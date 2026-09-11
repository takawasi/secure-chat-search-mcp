import pytest
from fastapi.testclient import TestClient
from secure_chat_search.config import Settings
from secure_chat_search.app import create_app
from secure_chat_search.auth import Principal

@pytest.fixture
def app():
    instance = create_app(Settings(database_url="sqlite:///:memory:"))
    yield instance
    instance.state.db.engine.dispose()

@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c

@pytest.fixture
def db(app): return app.state.db

@pytest.fixture
def service(app): return app.state.service

@pytest.fixture
def aoki(): return Principal("demo", "aoki")

@pytest.fixture
def sato(): return Principal("demo", "sato")

@pytest.fixture
def auth_headers(client):
    token = client.post("/api/demo/session", json={"subject": "aoki"}).json()["access_token"]
    return {"Authorization": "Bearer " + token}

@pytest.fixture
def mcp_headers(auth_headers):
    return {**auth_headers, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}

import os
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from secure_chat_search.auth import Auth, DemoMemberships
from secure_chat_search.core import Base, Database, Principal, Settings
from secure_chat_search.demo import seed
from secure_chat_search.search import SearchService


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    url = os.getenv("SCS_TEST_DATABASE_URL") or "sqlite:///" + str(tmp_path / "test.sqlite3")
    if os.getenv("SCS_TEST_DATABASE_URL"):
        # CIの使い捨て専用DB以外は破壊しない。
        assert url.endswith("/scs_test"), "PostgreSQLテストは専用scs_test DBのみ"
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
        engine.dispose()
    settings = Settings(_env_file=None, mode="demo", database_url=url, public_url="http://testserver")
    db = Database(settings)
    db.initialize()
    seed(db)
    auth = Auth(settings, DemoMemberships())
    service = SearchService(db, auth, settings)
    yield SimpleNamespace(db=db, auth=auth, settings=settings, service=service,
                          alice=Principal("alice"), bob=Principal("bob"))
    db.close()

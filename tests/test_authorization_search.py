import json
import pytest
from sqlalchemy import select
from secure_chat_search.core import Audit, DomainError, Message, Room, User
from secure_chat_search.demo import change_membership
from secure_chat_search.embeddings import cosine, index_messages
from secure_chat_search.search import SearchService


def test_history_and_recent(env):
    result = env.service.search(env.alice, "納期")
    assert len(result["results"]) == 4
    assert {x["metadata"]["source"] for x in result["results"]} == {"api", "export"}
    assert any("2023年の契約とは別案件" in x["text"] for x in result["results"])
    assert result["source_history_complete"] is False


def test_user_scoped_budget(env):
    assert env.service.search(env.alice, "予算")["results"] == []
    result = env.service.search(env.bob, "予算")
    assert len(result["results"]) == 1 and "850万円" in result["results"][0]["text"]
    with pytest.raises(DomainError) as exc:
        env.service.fetch(env.alice, result["results"][0]["id"])
    assert exc.value.status == 404


def test_departure_revokes_every_path(env):
    key = env.service.search(env.alice, "納期")["results"][0]["id"]
    assert env.service.fetch(env.alice, key)["text"]
    change_membership(env.db, "alice", False)
    assert env.service.search(env.alice, "納期")["results"] == []
    assert {r["room_id"] for r in env.service.status(env.alice)["rooms"]} == {"1003"}
    for action in [lambda: env.service.fetch(env.alice, key),
                   lambda: env.service.period(env.alice, "1001", 0, 2000000000)]:
        with pytest.raises(DomainError) as exc:
            action()
        assert exc.value.status == 404


@pytest.mark.parametrize("kind", ["disabled", "unapproved", "direct", "my"])
def test_disable_and_room_kind(env, kind):
    if kind == "disabled":
        with env.db.session() as session:
            session.get(User, "alice").enabled = False
        with pytest.raises(DomainError) as exc:
            env.service.search(env.alice, "納期")
        assert exc.value.status == 403
    else:
        with env.db.session() as session:
            room = session.get(Room, "1001")
            if kind == "unapproved":
                room.approved = False
            else:
                room.kind = kind
        assert env.service.search(env.alice, "納期")["results"] == []


def test_no_stale_permission_fallback(env):
    class Broken:
        def current_rooms(self, *args):
            raise RuntimeError("private upstream detail")
    env.auth.provider = Broken()
    with pytest.raises(DomainError) as exc:
        env.service.search(env.alice, "納期")
    assert exc.value.status == 503 and "private" not in exc.value.message


@pytest.mark.parametrize("query", ["%' OR 1=1 --", "%", "_", "\\", "DROP TABLE messages", "'; SELECT * FROM users --"])
def test_query_is_literal_not_sql(env, query):
    assert env.service.search(env.alice, query)["results"] == []
    assert len(env.service.search(env.alice, "納期")["results"]) == 4


@pytest.mark.parametrize("query", ["", " ", "x"*301, "[info][/info]"])
def test_invalid_query(env, query):
    with pytest.raises(DomainError):
        env.service.search(env.alice, query)


def test_normalized_and_query(env):
    assert env.service.search(env.bob, "ＡＰＩ 検索")["results"]
    assert not env.service.search(env.bob, "ＡＰＩ 存在しない文字列")["results"]


def test_time_author_filter(env):
    result = env.service.search(env.alice, "納期", since="2023-01-01T00:00:00+09:00",
                                until="2023-12-31T23:59:59+09:00", author_id="102")
    assert len(result["results"]) == 1 and "7月15日" in result["results"][0]["text"]


@pytest.mark.parametrize("since,until", [("2023-01-01", None), (200, 100), ("garbage", None)])
def test_bad_time_filter(env, since, until):
    with pytest.raises(DomainError):
        env.service.search(env.alice, "納期", since=since, until=until)


def test_period_is_paged_and_scope_bound(env):
    ids, cursor = [], None
    while True:
        result = env.service.period(env.alice, "1001", 0, 2000000000, cursor, 2)
        ids += [x["id"] for x in result["results"]]
        cursor = result["next_cursor"]
        assert result["source_history_complete"] is False
        if not cursor:
            assert result["stored_range_exhausted"]
            break
    assert len(ids) == len(set(ids)) == 5
    page = env.service.period(env.alice, "1001", 0, 2000000000, limit=1)
    with pytest.raises(DomainError):
        env.service.period(env.alice, "1001", 1, 2000000000, page["next_cursor"])


def test_context_does_not_cross_rooms(env):
    item = env.service.search(env.alice, "納期")["results"][0]
    result = env.service.fetch(env.alice, item["id"], context=5)
    assert all(x["metadata"]["room_id"] == "1001" for x in result["context"])
    assert result["metadata"]["untrusted_content"] is True


def test_audit_does_not_store_query_or_body(env):
    env.service.search(env.alice, "private-query-string")
    with env.db.session() as session:
        rows = list(session.scalars(select(Audit)))
        assert rows
        dump = json.dumps([{c.name: getattr(row, c.name) for c in Audit.__table__.columns} for row in rows])
        assert "private-query-string" not in dump and "850" not in dump


def test_demo_links_do_not_impersonate_chatwork(env):
    result = env.service.search(env.alice, "納期")
    assert all(x["url"].startswith("http://testserver/records/") for x in result["results"])


def test_semantic_must_be_explicit(env):
    with pytest.raises(DomainError) as exc:
        env.service.search(env.alice, "納期", mode="semantic")
    assert exc.value.code == "semantic_disabled"


class ExplicitTestEmbedder:
    """テスト専用。製品の意味検索として使わない。実モデルは別のsmokeで検証する。"""
    model_name = "unit-test-only-vector"
    def passages(self, texts):
        return [[1.0, 0.0] if "納期" in t else [0.0, 1.0] if "予算" in t else [0.1, 0.1] for t in texts]
    def query(self, text):
        return [0.0, 1.0] if "予算" in text else [1.0, 0.0]


def test_semantic_acl_and_index_idempotence(env):
    embedder = ExplicitTestEmbedder()
    assert index_messages(env.db, embedder)["indexed"] > 0
    assert index_messages(env.db, embedder)["indexed"] == 0
    service = SearchService(env.db, env.auth, env.settings, embedder)
    result = service.search(env.alice, "配送予定を変更する", mode="semantic")
    assert result["results"] and result["semantic_unindexed"] == 0
    assert all(x["metadata"]["room_id"] in {"1001", "1003"} for x in result["results"])
    assert not any("850" in x["text"] for x in result["results"])
    assert "850" in service.search(env.bob, "予算", mode="hybrid")["results"][0]["text"]


def test_unindexed_count_and_scope_limit(env):
    service = SearchService(env.db, env.auth, env.settings, ExplicitTestEmbedder())
    result = service.search(env.alice, "配送", mode="semantic")
    assert result["results"] == [] and result["semantic_unindexed"] == 6
    env.settings.semantic_limit = 2
    with pytest.raises(DomainError) as exc:
        service.search(env.alice, "配送", mode="semantic")
    assert exc.value.code == "semantic_scope"


@pytest.mark.parametrize("a,b", [([], []), ([1], [1, 2]), ([float('nan')], [1])])
def test_bad_vector(a, b):
    with pytest.raises(DomainError):
        cosine(a, b)

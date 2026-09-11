import pytest
from sqlalchemy import select, func
from secure_chat_search.auth import Principal, Policy
from secure_chat_search.db import Membership, Message, Room, User, Audit
from secure_chat_search.ingest import Record, upsert
from secure_chat_search.errors import AppError, UpstreamError
from secure_chat_search.search import SearchService

def test_old_history_and_reference(service, aoki):
    results = service.search(aoki, "納期")["results"]
    assert results and any(x["remote_id"] == "10103" for x in results)
    data = service.fetch(aoki, results[0]["id"])
    assert "12月15日" in data["text"] and data["url"]
    assert data["metadata"]["untrusted_source_text"] is True

def test_same_query_different_users(service, aoki, sato):
    assert service.search(aoki, "星野商事", "keyword")["results"]
    assert not service.search(sato, "星野商事", "keyword")["results"]
    assert service.search(Principal("demo", "mori"), "予算", "keyword")["results"]
    assert not service.search(sato, "採用計画", "keyword")["results"]

def test_fetch_and_timeline_reject_cross_room(service, aoki, sato):
    mid = service.search(aoki, "星野商事")["results"][0]["id"]
    with pytest.raises(AppError) as e: service.fetch(sato, mid)
    assert e.value.status == 404
    with pytest.raises(AppError): service.timeline(sato, "101", 0, 2000000000)

def test_membership_revocation_affects_fetch(db, service, aoki):
    mid = service.search(aoki, "星野商事")["results"][0]["id"]
    with db.session.begin() as s: s.get(Membership, ("demo", "aoki", "101")).enabled = False
    assert not service.search(aoki, "星野商事", "keyword")["results"]
    with pytest.raises(AppError): service.fetch(aoki, mid)

def test_allowlist_revocation(db, service, aoki):
    with db.session.begin() as s: s.get(User, ("demo", "aoki")).enabled = False
    with pytest.raises(AppError) as e: service.search(aoki, "納期")
    assert e.value.status == 403

def test_unapproved_room_cannot_be_requested(service, sato):
    assert service.search(sato, "納期", room_id="101")["results"] == []

def test_direct_and_my_never_stored(db, service, aoki):
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Message).where(Message.room_id.in_(["404", "405"]))) == 0
    assert all(x["id"] not in ("404", "405") for x in service.summary(aoki)["rooms"])

def test_room_disabled_even_if_membership_active(db, service, aoki):
    with db.session.begin() as s: s.get(Room, ("demo", "101")).enabled = False
    assert service.search(aoki, "星野商事", "keyword")["results"] == []

def test_tenant_boundary(db, service, aoki):
    with db.session.begin() as s:
        s.add(User(tenant="other", subject="aoki", name="other", account_id="11"))
        s.add(Room(tenant="other", room_id="101", name="other", kind="group", enabled=True)); s.flush()
        s.add(Membership(tenant="other", subject="aoki", room_id="101", enabled=True))
        upsert(s, "other", Record("101", "10103", "11", "other", 10, 10, "他社機密XYZ"), 10)
    assert service.search(aoki, "他社機密XYZ", "keyword")["results"] == []
    assert service.search(Principal("other", "aoki"), "他社機密XYZ", "keyword")["results"]

@pytest.mark.parametrize("query", ["' OR 1=1 --", "%", "_", "'; DROP TABLE messages;--"])
def test_sql_literal_not_execution(service, aoki, query):
    assert service.search(aoki, query, "keyword")["results"] == []
    assert service.summary(aoki)["visible_messages"] > 0

def test_normalization_fullwidth(service, aoki):
    assert service.search(aoki, "ＨＳ－２０４", "keyword")["results"]

def test_related_terms_are_labeled(service, aoki):
    result = service.search(aoki, "期限")
    assert result["results"] and result["meta"]["mode"] == "related"
    assert result["meta"]["generated_answer"] is False

def test_vector_unconfigured_fails_not_fakes(service, aoki):
    with pytest.raises(AppError) as e: service.search(aoki, "納期", "hybrid")
    assert e.value.code == "embedding_not_configured"

def test_semantic_receives_only_acl_permitted_data(app, db, service, sato):
    class FakeEmbedder:
        texts = []
        def encode(self, texts):
            self.texts = texts
            return [[1.0, float(i % 2)] for i in range(len(texts))]
    fake = FakeEmbedder()
    engine = SearchService(db, service.policy, app.state.settings, fake)
    result = engine.search(sato, "納期", "hybrid")
    assert result["meta"]["mode"] == "hybrid"
    assert not any("星野商事" in x or "採用計画" in x for x in fake.texts)
    assert {r["room_id"] for r in result["results"]}.issubset({"100", "202"})

def test_revoke_while_embedding_prevents_results(app, db, service, aoki):
    class Revoke:
        def encode(self, texts):
            with db.session.begin() as s: s.get(Membership, ("demo", "aoki", "101")).enabled = False
            return [[1., 0.] for _ in texts]
    engine = SearchService(db, service.policy, app.state.settings, Revoke())
    assert not any(r["room_id"] == "101" for r in engine.search(aoki, "納期", "hybrid")["results"])

def test_acl_failure_closed(app, db, aoki):
    class Failure:
        def current_group_rooms(self, *args): raise OSError("hidden secret")
    service = SearchService(db, Policy(db, Failure()), app.state.settings)
    with pytest.raises(UpstreamError) as e: service.search(aoki, "納期")
    assert "hidden secret" not in str(e.value)

def test_timeline_pagination(service, aoki):
    first = service.timeline(aoki, "101", 0, 2000000000, limit=2)
    second = service.timeline(aoki, "101", 0, 2000000000, offset=first["next_offset"], limit=2)
    assert first["has_more"] and len(first["results"]) == 2
    assert not {r["id"] for r in first["results"]} & {r["id"] for r in second["results"]}
    assert first["results"][0]["sent_at"] <= first["results"][1]["sent_at"]

def test_search_limits_disclosed(service, aoki):
    result=service.search(aoki, "納期", limit=1)
    assert result["meta"]["truncated"] and len(result["results"]) == 1

def test_query_and_period_validation(service, aoki):
    for kwargs in [{"query":""}, {"query":"x"*301}, {"query":"x", "mode":"unknown"}, {"query":"x","start":20,"end":10}]:
        with pytest.raises(AppError): service.search(aoki, **kwargs)

def test_audit_contains_no_query_or_body(db, service, aoki):
    service.search(aoki, "星野商事")
    with db.session() as s:
        event = s.scalars(select(Audit)).first()
        assert event.action == "search" and event.subject == "aoki"
        assert not {"query", "body", "token"} & set(Audit.__table__.columns.keys())

def test_sender_time_filter(service, aoki):
    result = service.search(aoki, "納期", sender="44", start=0, end=2000000000)
    assert result["results"] and all(x["sender"] == "高橋" for x in result["results"])

def test_idless_warning(service, aoki):
    result=service.search(aoki, "旧資料室", "keyword")
    assert result["results"] and result["meta"]["warnings"]

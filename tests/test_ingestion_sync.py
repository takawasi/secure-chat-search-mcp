import base64
import csv
import hashlib
import hmac
import io
import json
import pytest
from sqlalchemy import func, select
from secure_chat_search.core import DomainError, Message, Room, WebhookEvent, timestamp
from secure_chat_search.demo import api_message, sample_export
from secure_chat_search.ingestion import Incoming, import_csv, upsert
from secure_chat_search.sync import apply_messages, drain_webhooks, receive_webhook


def export(rows, **changes):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=["room_id", "account_id", "author", "sent_at", "updated_at", "message_id", "body", "deleted"])
    writer.writeheader()
    for row in rows:
        writer.writerow({"room_id":"1001", "account_id":"101", "author":"佐藤", "sent_at":"2023-05-10T09:00:00+09:00", "deleted":"false", **row})
    manifest = {"snapshot_at":"2026-09-02T04:00:00+09:00", "coverage_from":"2023-01-01T00:00:00+09:00",
                "coverage_through":"2026-09-02T04:00:00+09:00", "room_ids":["1001"], "complete_snapshot":True}
    manifest.update(changes)
    return out.getvalue().encode(), manifest


def visible_count(db, room="1001"):
    with db.session() as session:
        return session.scalar(select(func.count()).select_from(Message).where(Message.room_id==room, Message.deleted.is_(False)))


def test_replay_is_idempotent(env):
    raw, manifest = sample_export()
    before = visible_count(env.db)
    result = import_csv(env.db, raw, manifest)
    assert result["replayed"] and visible_count(env.db) == before


def test_dm_my_and_unapproved_never_imported(env):
    with env.db.session() as session:
        assert not list(session.scalars(select(Message).where(Message.room_id.in_(["9001","9002","9003"]))))


def test_snapshot_replaces_idless_and_wipes_old_body(env):
    with env.db.session() as session:
        old = list(session.scalars(select(Message.id).where(Message.room_id=="1001", Message.external_id.is_(None))))
    raw, manifest = export([{"body":"改訂した契約の確認です。"}])
    import_csv(env.db, raw, manifest)
    assert visible_count(env.db)==2  # 新しい履歴1件と、範囲外の最新API1件。
    with env.db.session() as session:
        assert all(session.get(Message, key).body=="" and session.get(Message,key).deleted for key in old)


def test_invalid_csv_is_atomic(env):
    before = visible_count(env.db)
    raw, manifest = export([{"body":"valid"}, {"account_id":"../../invalid", "body":"bad"}])
    with pytest.raises(DomainError):
        import_csv(env.db, raw, manifest)
    assert visible_count(env.db)==before


def test_stale_export_is_rejected(env):
    raw, manifest = export([{"body":"古いsnapshot"}], snapshot_at="2025-01-01T00:00:00+09:00",
                           coverage_through="2025-01-01T00:00:00+09:00")
    with pytest.raises(DomainError) as exc:
        import_csv(env.db, raw, manifest)
    assert exc.value.code=="stale_snapshot"


def test_snapshot_must_be_complete(env):
    raw, manifest = export([{"body":"partial"}], complete_snapshot=False)
    with pytest.raises(DomainError) as exc:
        import_csv(env.db, raw, manifest)
    assert exc.value.code=="incomplete_snapshot"


def test_ambiguous_same_second_posts_are_not_collapsed(env):
    raw, manifest = export([{"body":"同じ発言"},{"body":"同じ発言"}])
    import_csv(env.db, raw, manifest)
    sent=timestamp("2023-05-10T09:00:00+09:00")
    apply_messages(env.db,"1001",[api_message("8000","101","佐藤",sent,"同じ発言")],timestamp("2026-09-03T00:00:00+09:00"))
    with env.db.session() as session:
        assert session.scalar(select(func.count()).select_from(Message).where(Message.body=="同じ発言",Message.deleted.is_(False)))==3


def test_unambiguous_overlap_is_matched(env):
    raw, manifest = export([{"body":"一意の発言"}])
    import_csv(env.db, raw, manifest)
    sent=timestamp("2023-05-10T09:00:00+09:00")
    apply_messages(env.db,"1001",[api_message("8001","101","佐藤",sent,"一意の発言")],timestamp("2026-09-03T00:00:00+09:00"))
    with env.db.session() as session:
        assert session.scalar(select(func.count()).select_from(Message).where(Message.body=="一意の発言",Message.deleted.is_(False)))==1


def test_edit_clears_vector_old_message_does_not_overwrite(env):
    with env.db.session() as session:
        upsert(session,Incoming("1001","9990","101","佐藤",100,200,"改訂版"),200,"api")
    with env.db.session() as session:
        item=session.get(Message,"cw:1001:9990")
        item.embedding,item.embedding_model=[1,0],"test"
        assert not upsert(session,Incoming("1001","9990","101","佐藤",100,100,"古い本文"),300,"api")
    with env.db.session() as session:
        item=session.get(Message,"cw:1001:9990")
        assert item.body=="改訂版" and item.embedding==[1,0]
        upsert(session,Incoming("1001","9990","101","佐藤",100,400,"新しい本文"),400,"api")
    with env.db.session() as session:
        assert session.get(Message,"cw:1001:9990").embedding is None


def test_explicit_delete_wipes_and_does_not_resurrect(env):
    with env.db.session() as session:
        upsert(session,Incoming("1001","9991","101","佐藤",100,100,"消す本文"),100,"api")
    with env.db.session() as session:
        item=session.get(Message,"cw:1001:9991")
        item.embedding,item.embedding_model=[1,0],"test"
        upsert(session,Incoming("1001","9991","101","佐藤",100,200,"",True),200,"export")
    with env.db.session() as session:
        assert not upsert(session,Incoming("1001","9991","101","佐藤",100,100,"消す本文"),300,"api")
        item=session.get(Message,"cw:1001:9991")
        assert item.deleted and item.body=="" and item.embedding is None and item.refs==[]


def test_poll_overflow_is_visible_and_not_cleared_by_next_poll(env):
    at=timestamp("2026-09-12T00:00:00+09:00")
    payloads=[api_message(str(10000+i),"101","佐藤",at+i,"大量発言") for i in range(100)]
    result=apply_messages(env.db,"1001",payloads,at+200)
    assert result["gap_suspected"]
    assert apply_messages(env.db,"1001",payloads[-1:],at+300)["gap_suspected"]
    assert any(r["gap_suspected"] for r in env.service.status(env.alice)["rooms"])


def test_empty_poll_does_not_delete(env):
    before=visible_count(env.db)
    apply_messages(env.db,"1001",[],2000000000)
    assert visible_count(env.db)==before


@pytest.mark.parametrize("room",["9001","9002","9003","999999"])
def test_nonapproved_sync_is_denied(env,room):
    with pytest.raises(DomainError) as exc:
        apply_messages(env.db,room,[],2000000000)
    assert exc.value.status==403


def webhook(body="通知された本文",updated=0):
    payload={"webhook_event_type":"message_created" if not updated else "message_updated",
             "webhook_event_time":max(1000,updated),
             "webhook_event":{"room_id":1001,"account_id":101,"message_id":"9998","send_time":100,
                              "update_time":updated,"body":body}}
    raw=json.dumps(payload,ensure_ascii=False).encode()
    token=base64.b64encode(b"unit-test-secret-not-a-real-credential").decode()
    signature=base64.b64encode(hmac.new(base64.b64decode(token),raw,hashlib.sha256).digest()).decode()
    return raw,signature,token


def test_webhook_persists_before_processing_and_deduplicates(env):
    raw,sig,token=webhook()
    assert receive_webhook(env.db,raw,sig,token)["accepted"]
    assert receive_webhook(env.db,raw,sig,token)["duplicate"]
    with env.db.session() as session:
        assert session.get(Message,"cw:1001:9998") is None
        assert session.scalar(select(WebhookEvent)).payload
    assert drain_webhooks(env.db)=={"succeeded":1,"failed":0}
    with env.db.session() as session:
        assert session.get(Message,"cw:1001:9998").body=="通知された本文"
        event=session.scalar(select(WebhookEvent))
        assert event.done and event.payload=={}


def test_webhook_wrong_signature_rejected(env):
    raw,_,token=webhook()
    with pytest.raises(DomainError) as exc:
        receive_webhook(env.db,raw,"wrong",token)
    assert exc.value.status==401


def test_webhook_out_of_order_keeps_newer(env):
    receive_webhook(env.db,*webhook("新しい本文",500))
    drain_webhooks(env.db)
    receive_webhook(env.db,*webhook("古い本文",200))
    drain_webhooks(env.db)
    with env.db.session() as session:
        assert session.get(Message,"cw:1001:9998").body=="新しい本文"


def test_later_approval_allows_same_file_import(env):
    raw,manifest=export([{"room_id":"9003","body":"後で承認した部屋"}],room_ids=["9003"])
    assert import_csv(env.db,raw,manifest)["imported"]==0
    with env.db.session() as session:
        session.get(Room,"9003").approved=True
    assert import_csv(env.db,raw,manifest)["imported"]==1


@pytest.mark.parametrize("configured",[False,True])
def test_deleted_marker_is_explicit_not_guessed(env,configured):
    raw,manifest=export([{"body":"削除されました"}])
    if configured:
        manifest["deleted_marker"]="削除されました"
    import_csv(env.db,raw,manifest)
    with env.db.session() as session:
        count=session.scalar(select(func.count()).select_from(Message).where(Message.body=="削除されました",Message.deleted.is_(False)))
        assert count==(0 if configured else 1)

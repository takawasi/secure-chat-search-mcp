import base64
import hashlib
import hmac
import json
import secrets
import pytest
import httpx
from sqlalchemy import select
from secure_chat_search.sync import Synchronizer, WebhookInbox
from secure_chat_search.chatwork import ChatworkClient, verify_signature, record_from_api
from secure_chat_search.db import Inbox, SyncState, Message
from secure_chat_search.errors import AppError, UpstreamError
from secure_chat_search.embeddings import HttpEmbedder, rrf, validate_vectors

def msg(mid="900",body="同期試験",sent=100,updated=0):
    return {"message_id":mid,"account":{"account_id":11,"name":"青木"},"send_time":sent,"update_time":updated,"body":body}

def test_sync_idempotent_and_edit(db, service, aoki):
    sync=Synchronizer(db)
    assert sync.apply_recent("demo","101",[msg()],200)["inserted"]==1
    assert sync.apply_recent("demo","101",[msg()],210)["unchanged"]==1
    assert sync.apply_recent("demo","101",[msg(body="改訂同期",updated=250)],300)["updated"]==1
    assert sync.apply_recent("demo","101",[msg()],400)["stale"]==1
    assert service.search(aoki,"改訂同期","keyword")["results"]

def test_100_new_messages_warn(db,service,aoki):
    rows=[msg(str(90000+i),sent=100+i) for i in range(100)]
    out=Synchronizer(db).apply_recent("demo","101",rows,500)
    assert out["gap_suspected"]
    assert service.search(aoki,"同期試験")["meta"]["warnings"]

def test_overlap_not_clear_existing_gap(db):
    sync=Synchronizer(db);rows=[msg(str(90000+i),sent=100+i) for i in range(100)]
    sync.apply_recent("demo","101",rows,500)
    assert sync.apply_recent("demo","101",rows,600)["gap_suspected"]

def test_overlap_prevents_new_gap_flag(db):
    sync=Synchronizer(db);rows=[msg(str(90000+i),sent=100+i) for i in range(100)]
    sync.apply_recent("demo","101",rows[:1],300)
    assert not sync.apply_recent("demo","101",rows,500)["gap_suspected"]

def test_204_no_records_is_valid(db):
    assert Synchronizer(db).apply_recent("demo","101",[],500)["received"]==0

def test_non_group_sync_rejected(db):
    with pytest.raises(AppError):Synchronizer(db).apply_recent("demo","404",[msg()],300)

class Tokens:
    def get_access_token(self,p):return "test-only-not-a-real-token"

@pytest.mark.parametrize("status,code",[(401,"chatwork_access_revoked"),(403,"chatwork_access_revoked"),(429,"chatwork_rate_limited"),(500,"chatwork_unavailable"),(302,"chatwork_unavailable")])
def test_upstream_errors_do_not_leak(aoki,status,code):
    cw=ChatworkClient(Tokens(),httpx.MockTransport(lambda r:httpx.Response(status,text="private upstream text")))
    with pytest.raises(UpstreamError) as e:cw.recent(aoki,"101")
    assert e.value.code==code and "private upstream" not in str(e.value)

def test_api_force_and_oauth_header(aoki):
    def handler(r):
        assert r.url.host=="api.chatwork.com"
        assert r.headers["Authorization"].startswith("Bearer ")
        assert r.url.params["force"]=="1"
        return httpx.Response(204)
    assert ChatworkClient(Tokens(),httpx.MockTransport(handler)).recent(aoki,"101")==[]

def test_live_rooms_me_binding(aoki):
    def handler(r):
        if r.url.path.endswith("/me"):return httpx.Response(200,json={"account_id":11})
        return httpx.Response(200,json=[{"room_id":101,"type":"group"},{"room_id":404,"type":"direct"}])
    cw=ChatworkClient(Tokens(),httpx.MockTransport(handler))
    assert cw.current_group_rooms(aoki,"11")=={"101"}
    with pytest.raises(UpstreamError):cw.current_group_rooms(aoki,"22")

def signed_event(room=101,body="Webhook本文",updated=0):
    secret=secrets.token_bytes(32);token=base64.b64encode(secret).decode()
    data={"webhook_setting_id":"test","webhook_event_type":"message_created","webhook_event_time":100,
          "webhook_event":{"room_id":room,"message_id":"999","account_id":11,"send_time":100,"update_time":updated,"body":body}}
    raw=json.dumps(data,ensure_ascii=False).encode()
    sig=base64.b64encode(hmac.new(secret,raw,hashlib.sha256).digest()).decode()
    return raw,sig,token

def test_signature_raw_body_and_tamper():
    raw,sig,token=signed_event();verify_signature(raw,sig,token)
    with pytest.raises(AppError):verify_signature(raw+b" ",sig,token)
    with pytest.raises(AppError):verify_signature(raw,"bad",token)

def test_webhook_queued_once_and_processed(db,service,aoki):
    raw,sig,token=signed_event();inbox=WebhookInbox(db,token,"demo")
    assert not inbox.accept(raw,sig)["duplicate"]
    assert inbox.accept(raw,sig)["duplicate"]
    assert not service.search(aoki,"Webhook本文","keyword")["results"]
    assert inbox.drain()["processed"]==1
    assert service.search(aoki,"Webhook本文","keyword")["results"]
    assert inbox.drain()["processed"]==0
    with db.session() as s:
        event=s.scalars(select(Inbox)).first();assert event.state=="processed" and event.payload=={}

def test_webhook_direct_not_queued(db):
    raw,sig,token=signed_event(404)
    assert not WebhookInbox(db,token,"demo").accept(raw,sig)["accepted"]

def test_webhook_pending_survives_new_instance(db):
    raw,sig,token=signed_event();WebhookInbox(db,token,"demo").accept(raw,sig)
    assert WebhookInbox(db,token,"demo").drain()["processed"]==1

def test_embedding_real_http_contract_without_claiming_real_model():
    def handler(r):
        payload=json.loads(r.content)
        assert payload["model"]=="test-contract" and payload["input"]==["質問","本文"]
        return httpx.Response(200,json={"data":[{"index":1,"embedding":[0.,2.]},{"index":0,"embedding":[3.,0.]}]})
    embed=HttpEmbedder("https://embedding.example/embeddings","test-contract",transport=httpx.MockTransport(handler))
    assert embed.encode(["質問","本文"])==[[1.,0.],[0.,1.]]

@pytest.mark.parametrize("vectors",[[[0.,0.]],[[float("nan"),1]],[[float("inf"),1]],[[True,1]]])
def test_invalid_vectors_rejected(vectors):
    with pytest.raises(UpstreamError):validate_vectors(vectors,1)

def test_rrf_no_double_count():
    scores=rrf([["a","a","b"],["b","a"]])
    assert scores["a"]==scores["b"]

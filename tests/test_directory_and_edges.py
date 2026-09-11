import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql
from secure_chat_search.directory import configure
from secure_chat_search.db import User, Room, Base, Database, SyncState
from secure_chat_search.config import Settings
from secure_chat_search.errors import AppError, UpstreamError
from secure_chat_search.chatwork import record_from_api
from secure_chat_search.embeddings import validate_vectors
from secure_chat_search.sync import Synchronizer


def test_directory_configure_and_explicit_revocation(db):
    manifest={"tenant":"example","users":[{"subject":"id-1","name":"検証利用者","account_id":"8001"}],"rooms":[{"room_id":"8801","name":"検証ルーム"}]}
    result=configure(db,manifest)
    assert result["users_updated"]==1 and result["rooms_updated"]==1
    manifest["users"][0]["enabled"]=False;manifest["rooms"][0]["enabled"]=False
    configure(db,manifest)
    with db.session() as s:
        assert not s.get(User,("example","id-1")).enabled
        assert not s.get(Room,("example","8801")).enabled
        assert s.get(User,("demo","aoki")).enabled


@pytest.mark.parametrize("document",[{},None,[],{"tenant":"t","users":[{}]}, {"tenant":"t","users":"x"}, {"tenant":"t","rooms":[{"room_id":"1","name":"A","kind":"anything"}]}, {"tenant":"t","rooms":[{"room_id":"1","name":3}]}])
def test_invalid_directory_fails_closed(db,document):
    with pytest.raises(AppError):configure(db,document)


def test_directory_validation_is_atomic(db):
    document={"tenant":"t","users":[{"subject":"s","name":"n","account_id":"1"}],"rooms":[{"room_id":"not-id","name":"r"}]}
    with pytest.raises(AppError):configure(db,document)
    with db.session() as s:assert s.get(User,("t","s")) is None


@pytest.mark.parametrize("payload",[None,[],"body",{"account":None},{"account":[]},{"account":"x"},{}])
def test_malformed_upstream_message_is_classified(payload):
    with pytest.raises(UpstreamError):record_from_api("100",payload)


@pytest.mark.parametrize("vectors",[None,{},[None],[[1],None],[[1e308,1e308,1e308,1e308]]])
def test_malformed_embedding_vectors_are_classified(vectors):
    with pytest.raises(UpstreamError):validate_vectors(vectors,2 if vectors==[[1],None] else 1)


@pytest.mark.parametrize("url",["https://u:p@example.test", "https://example.test/path", "https://example.test?q=secret", "https://example.test#x"])
def test_base_url_must_be_origin(url):
    with pytest.raises(ValueError):Settings(base_url=url).validate()


def test_postgres_ddl_compiles_without_claiming_live_connection():
    for table in Base.metadata.sorted_tables:
        sql=str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE" in sql
    sql=str(CreateTable(Base.metadata.tables["messages"]).compile(dialect=postgresql.dialect()))
    assert "BIGINT" in sql and "uq_remote_message" in sql


def test_sync_failure_recorded(db,aoki,service):
    class Failed:
        def recent(self,p,room):raise UpstreamError("chatwork_rate_limited")
    with pytest.raises(UpstreamError):Synchronizer(db).poll(aoki,"101",Failed(),service.policy)
    with db.session() as s:assert s.get(SyncState,("demo","101")).error_code=="chatwork_rate_limited"


def test_xss_payload_stored_as_untrusted_text(db,client,auth_headers):
    from secure_chat_search.ingest import Record,upsert
    body='<img src=x onerror="window.__xssExecuted=true"> XSS検証'
    with db.session.begin() as s:
        upsert(s,"demo",Record("101","888888","11","検証",100,100,body),200)
    result=client.post('/api/search',headers=auth_headers,json={"query":"XSS検証"}).json()["results"][0]
    original=client.get('/api/messages/'+result["id"],headers=auth_headers).json()
    assert original["text"]==body and original["metadata"]["untrusted_source_text"]


def test_cli_init_configure_and_invalid_input(tmp_path):
    root=Path(__file__).resolve().parents[1]
    env={**os.environ,"PYTHONPATH":str(root/"src"),"SCS_MODE":"demo","SCS_DATABASE_URL":"sqlite:///"+str(tmp_path/"cli.db")}
    def run(*args):
        return subprocess.run([sys.executable,"-m","secure_chat_search.cli",*args],cwd=root,env=env,text=True,capture_output=True,timeout=15)
    assert run("init-db").returncode==0
    f=tmp_path/"directory.json";f.write_text(json.dumps({"tenant":"t","users":[{"subject":"u","name":"n","account_id":"1"}],"rooms":[{"room_id":"1","name":"r"}]}))
    assert run("configure",str(f)).returncode==0
    f.write_text("not-json")
    invalid=run("configure",str(f))
    assert invalid.returncode==1 and "Traceback" not in invalid.stderr
    unsafe=run("serve","--host","0.0.0.0")
    assert unsafe.returncode==1 and "unsafe_demo_bind" in unsafe.stderr


def test_old_history_remains_searchable_after_latest_100_sync(db,service,aoki):
    old=service.search(aoki,"12月15日","keyword")["results"][0]["id"]
    rows=[{"message_id":str(990000+i),"account":{"account_id":11},"send_time":1800000000+i,"update_time":0,"body":"別の最新投稿"} for i in range(100)]
    result=Synchronizer(db).apply_recent("demo","101",rows,1800000100)
    assert result["gap_suspected"]
    assert service.fetch(aoki,old)["text"]

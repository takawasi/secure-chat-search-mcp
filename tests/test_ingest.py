import csv
import io
import pytest
from sqlalchemy import select, func
from secure_chat_search.ingest import Importer, Record, upsert
from secure_chat_search.demo import history_csv
from secure_chat_search.text import timestamp, references
from secure_chat_search.db import Message
from secure_chat_search.errors import AppError

def csv_data(rows):
    b=io.StringIO(); w=csv.writer(b)
    w.writerow(["room_id","message_id","sender_id","sender_name","sent_at","updated_at","body","status"])
    w.writerows(rows);return b.getvalue().encode()

def row(mid="777", body="検証本文", updated="101", status="active", room="101"):
    return [room,mid,"11","青木","100",updated,body,status]

def test_repeat_import_idempotent(db):
    importer=Importer(db); data=csv_data([row()])
    first=importer.import_csv(data,"demo",200);second=importer.import_csv(data,"demo",200)
    assert first["inserted"]==1 and second["replayed"]
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Message).where(Message.remote_id=="777"))==1

def test_edit_and_no_rollback(db, service, aoki):
    importer=Importer(db)
    importer.import_csv(csv_data([row(body="旧本文")]),"demo",200)
    importer.import_csv(csv_data([row(body="新本文",updated="250")]),"demo",300)
    result=importer.import_csv(csv_data([row(body="古い別内容",updated="110")]),"demo",400)
    assert result["stale"]==1
    assert service.search(aoki,"新本文","keyword")["results"]
    assert not service.search(aoki,"旧本文","keyword")["results"]

def test_delete_removes_body_and_fetch(db, service, aoki):
    importer=Importer(db);importer.import_csv(csv_data([row()]),"demo",200)
    mid=service.search(aoki,"検証本文","keyword")["results"][0]["id"]
    importer.import_csv(csv_data([row(updated="250",status="deleted")]),"demo",300)
    with pytest.raises(AppError):service.fetch(aoki,mid)
    with db.session() as s:
        message=s.get(Message,mid); assert message.body=="" and message.normalized=="" and message.deleted

def test_delete_not_resurrected_by_old_event(db):
    with db.session.begin() as s:
        upsert(s,"demo",Record("101","999","11","name",10,20,"",True),20)
        result=upsert(s,"demo",Record("101","999","11","name",10,20,"old"),30)
        assert result=="stale"

def test_idless_requires_explicit_scope(db):
    with pytest.raises(AppError) as e:Importer(db).import_csv(csv_data([row(mid="")]),"demo",200)
    assert e.value.code=="idless_requires_replace"

def test_idless_replace_not_merge_duplicates(db, service, aoki):
    importer=Importer(db)
    # 完全に同じ発言が2つあっても、別行として保存する。
    importer.import_csv(csv_data([row(mid="",body="同文履歴"),row(mid="",body="同文履歴")]),"demo",200,replace_rooms=["101"])
    assert len(service.search(aoki,"同文履歴","keyword")["results"])==2
    importer.import_csv(csv_data([row(mid="",body="置換後履歴",updated="250")]),"demo",300,replace_rooms=["101"])
    assert not service.search(aoki,"同文履歴","keyword")["results"]
    assert service.search(aoki,"置換後履歴","keyword")["results"]
    # ID付きの元デモ履歴を巻き添えで消さない。
    assert service.search(aoki,"星野商事","keyword")["results"]

def test_old_snapshot_replace_rejected(db):
    imp=Importer(db);data=csv_data([row(mid="")])
    imp.import_csv(data,"demo",300,replace_rooms=["101"])
    with pytest.raises(AppError) as e:imp.import_csv(data,"demo",200,replace_rooms=["101"])
    assert e.value.status==409

def test_empty_snapshot_explicitly_clears_idless(db, service, aoki):
    imp=Importer(db);imp.import_csv(csv_data([row(mid="",body="消す履歴")]),"demo",200,replace_rooms=["101"])
    imp.import_csv(csv_data([]),"demo",300,replace_rooms=["101"])
    assert not service.search(aoki,"消す履歴","keyword")["results"]

def test_invalid_row_rolls_back_everything(db, service, aoki):
    bad=row(mid="bad")
    with pytest.raises(AppError):Importer(db).import_csv(csv_data([row(body="原子的取り込み"),bad]),"demo",200)
    assert not service.search(aoki,"原子的取り込み","keyword")["results"]

def test_late_idless_validation_rolls_back_known_row(db, service, aoki):
    with pytest.raises(AppError):Importer(db).import_csv(csv_data([row(body="巻戻し確認"),row(mid="")]),"demo",200)
    assert not service.search(aoki,"巻戻し確認","keyword")["results"]

def test_unknown_and_direct_rooms_excluded(db):
    result=Importer(db).import_csv(csv_data([row(room="404"),row(room="9999")]),"demo",200)
    assert result["excluded"]==2 and not result["inserted"]

def test_historical_body_is_not_indexed(db, service, aoki):
    Importer(db).import_csv(csv_data([row(body="編集前秘密",status="historical")]),"demo",200)
    assert not service.search(aoki,"編集前秘密","keyword")["results"]

@pytest.mark.parametrize("data",[b"a,b\n1,2\n",b"room_id,sender_id,sent_at,body,body\n",b"room_id,sender_id,sent_at,body\n101,11,100\n",b"\xff\xfe\xff"])
def test_bad_csv_rejected(db,data):
    with pytest.raises(AppError):Importer(db).import_csv(data,"demo",200)

def test_multiline_csv_preserved(db,service,aoki):
    Importer(db).import_csv(csv_data([row(body="複数行\n二行目,カンマ\"引用\"")]),"demo",200)
    result=service.search(aoki,"複数行","keyword")["results"][0]
    assert "\n二行目" in service.fetch(aoki,result["id"])["text"]

def test_cp932_explicit(db,service,aoki):
    data=csv_data([row(body="文字コード検証")]).decode().encode("cp932")
    Importer(db).import_csv(data,"demo",200,encoding="cp932")
    assert service.search(aoki,"文字コード検証","keyword")["results"]

@pytest.mark.parametrize("value",["2025-01-01T00:00:00", "not-date", -1, True])
def test_timezone_required(value):
    with pytest.raises(AppError):timestamp(value)

def test_timezone_equivalence():
    assert timestamp("2025-01-01T09:00:00+09:00")==timestamp("2025-01-01T00:00:00Z")

def test_reference_parsing():
    assert references("[rp aid=11 to=101-123]")[0]["message_id"]=="123"

def test_snapshot_future_row_rejected(db):
    with pytest.raises(AppError):Importer(db).import_csv(csv_data([row(updated="301")]),"demo",200)

def test_large_csv_rejected(db):
    with pytest.raises(AppError) as e:Importer(db).import_csv(b"x"*(2*1024*1024+1),"demo",200)
    assert e.value.status==413

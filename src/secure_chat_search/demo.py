"""個人・会社・会話はすべて架空。顧客データをデモへ取り込まない。"""
from __future__ import annotations
import csv
import io
import time
from sqlalchemy import delete, select
from .core import Audit, DomainError, ImportBatch, Membership, Message, Meta, Room, User, WebhookEvent, timestamp
from .ingestion import import_csv
from .sync import apply_messages


ROOMS = [
    ("1001", "星野商事 / 営業相談", "group", True),
    ("1002", "検索システム / 開発", "group", True),
    ("1003", "全社のお知らせ", "group", True),
    ("1004", "開発チーム / 予算", "group", True),
    ("9001", "非公開ダイレクト", "direct", False),
    ("9002", "マイチャット", "my", False),
    ("9003", "未承認グループ", "group", False),
]


def sample_export():
    rows = [
        ("1001", "101", "佐藤", "2023-05-10T09:00:00+09:00", "星野商事との初回相談です。希望納期は6月30日です。"),
        ("1001", "102", "田中", "2023-05-10T09:15:00+09:00", "部材調達の遅れがあり、納期を7月15日に変更する案を提案します。"),
        ("1001", "101", "佐藤", "2023-05-11T10:00:00+09:00", "星野商事と合意しました。納期は7月15日、分納なしで契約を更新します。"),
        ("1001", "102", "田中", "2024-03-01T13:00:00+09:00", "支払条件は月末締め、翌月末払いです。"),
        ("1002", "201", "鈴木", "2024-06-12T11:00:00+09:00", "検索APIは、検索前に現在参加しているルームで対象を制限します。"),
        ("1002", "202", "高橋", "2024-06-12T11:10:00+09:00", "本文の直接取得でも同じ認可が必要です。退室後はIDを知っていても取得を拒否します。"),
        ("1003", "301", "総務", "2025-04-01T09:00:00+09:00", "社内文書を参照する際は根拠と更新日を確認してください。"),
        ("1004", "201", "鈴木", "2025-08-20T10:00:00+09:00", "開発内部予算は850万円です。営業への共有対象ではありません。"),
        ("9001", "101", "佐藤", "2025-08-20T10:00:00+09:00", "ダイレクトチャットの除外確認用データ。"),
        ("9002", "101", "佐藤", "2025-08-20T10:00:00+09:00", "マイチャットの除外確認用データ。"),
        ("9003", "101", "佐藤", "2025-08-20T10:00:00+09:00", "未承認グループの除外確認用データ。"),
    ]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["room_id", "account_id", "author", "sent_at", "body"])
    writer.writerows(rows)
    manifest = {"snapshot_at": "2026-09-01T04:00:00+09:00", "coverage_from": "2023-01-01T00:00:00+09:00",
                "coverage_through": "2026-09-01T04:00:00+09:00", "room_ids": [r[0] for r in ROOMS],
                "complete_snapshot": True, "encoding": "utf-8-sig"}
    return output.getvalue().encode("utf-8-sig"), manifest


def api_message(key, account, author, sent, body, updated=0):
    return {"message_id": str(key), "account": {"account_id": account, "name": author},
            "send_time": sent, "update_time": updated, "body": body}


def seed(db, reset=False):
    with db.session() as session:
        marker = session.get(Meta, "dataset_kind")
        if not marker or marker.value != "synthetic":
            raise DomainError("not_demo", "実データ用DBにデモを作成できません。", 403)
        if session.scalar(select(User.sub).limit(1)) and not reset:
            return
        if reset:
            for model in [Audit, WebhookEvent, ImportBatch, Message, Membership, Room, User]:
                session.execute(delete(model))
        session.add_all([User(sub="alice", name="佐藤 / 営業", account_id="101", enabled=True),
                         User(sub="bob", name="鈴木 / 開発", account_id="201", enabled=True),
                         User(sub="disabled", name="利用停止済み", account_id="999", enabled=False)])
        session.add_all([Room(id=key, name=name, kind=kind, approved=approved) for key, name, kind, approved in ROOMS])
        session.flush()
        session.add_all([Membership(sub="alice", room_id=x) for x in ["1001", "1003", "9001", "9002", "9003"]] +
                        [Membership(sub="bob", room_id=x) for x in ["1002", "1003", "1004"]])
    raw, manifest = sample_export()
    import_csv(db, raw, manifest)
    sent = timestamp("2026-09-09T10:00:00+09:00")
    apply_messages(db, "1001", [api_message("7001", "101", "佐藤", sent,
        "星野商事の今年の追加発注は納期10月20日です。2023年の契約とは別案件なので注意してください。")], sent + 300)
    apply_messages(db, "1002", [api_message("7002", "201", "鈴木", sent,
        "API同期の監視を追加しました。100件で追いつけない可能性は欠損疑いとして表示します。")], sent + 300)


def change_membership(db, sub, joined):
    room = "1001" if sub == "alice" else "1002"
    with db.session() as session:
        existing = session.get(Membership, (sub, room))
        if joined and not existing:
            session.add(Membership(sub=sub, room_id=room))
        if not joined and existing:
            session.delete(existing)
    return {"room_id": room, "joined": joined}


def simulate_sync(db, sub):
    room, account, author = ("1001", "101", "佐藤") if sub == "alice" else ("1002", "201", "鈴木")
    sent = max(int(time.time()), timestamp("2026-09-11T12:00:00+09:00"))
    return apply_messages(db, room, [api_message(str(sent), account, author, sent,
        "最新ログの同期デモです。納期についての追加確認を受け付けました。")], sent)

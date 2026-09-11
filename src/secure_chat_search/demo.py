"""全件架空の実演用データ。実在する顧客のログは使わない。"""
import csv
import io
import time
from datetime import datetime
from sqlalchemy import delete, select
from .db import User, Room, Membership, Message, SyncState, ImportRun, Inbox, Audit
from .ingest import Record, upsert, Importer
from .sync import Synchronizer
from .text import timestamp

PEOPLE = [("aoki", "青木｜営業", "11"), ("sato", "佐藤｜開発", "22"), ("mori", "森｜人事", "33")]
ROOMS = [("100", "全社のお知らせ", "group"), ("101", "営業｜星野商事の導入", "group"),
         ("202", "開発｜検索基盤", "group"), ("303", "人事｜採用計画", "group"),
         ("404", "ダイレクトチャット（対象外）", "direct"), ("405", "マイチャット（対象外）", "my"),
         ("500", "資料室｜IDなし履歴", "group")]
MEMBERS = {"aoki": ["100", "101", "500", "404", "405"], "sato": ["100", "202"], "mori": ["100", "303"]}
# 匿名化した実ログではなく、初めから架空に書き起こした会話。
CONVERSATIONS = [
("100", "10001", "11", "青木", "2024-10-02T09:00:00+09:00", "社内チャット検索の目的は、以前の相談や決定の根拠にたどり着くことです。新しい情報と古い情報を区別しましょう。"),
("100", "10002", "22", "佐藤", "2025-01-15T10:00:00+09:00", "検索に表示できるのは現在参加中のグループだけです。退室すると以後の検索から除外します。"),
("100", "10003", "33", "森", "2026-06-01T10:00:00+09:00", "【運用方針】添付ファイルの本文は今回の検索対象外です。会話に書かれた説明を探してください。"),
("101", "10101", "11", "青木", "2024-11-05T09:30:00+09:00", "星野商事の初回導入について相談です。当初の納期は11月末、予算は80万円を見込んでいます。これはまだ検討案です。"),
("101", "10102", "44", "高橋", "2024-11-05T10:00:00+09:00", "[rp aid=11 to=101-10101]移行対象が増えたため、11月末の納品には間に合わない見込みです。移行試験を優先して日程を再調整しましょう。"),
("101", "10103", "11", "青木", "2024-11-06T14:00:00+09:00", "【決定】星野商事と合意し、納期を12月15日に変更します。理由は既存データの移行試験に追加期間が必要なためです。費用は90万円で確定しました。"),
("101", "10104", "44", "高橋", "2024-12-15T17:00:00+09:00", "星野商事への納品と受入確認が完了しました。未解決の課題はなく、翌月から保守へ移行します。"),
("101", "10105", "11", "青木", "2025-07-01T11:00:00+09:00", "案件番号 HS-204 の更新契約です。前年の導入費用と混同しないでください。今回の保守契約は月額3万円です。"),
("101", "10106", "44", "高橋", "2026-08-20T13:00:00+09:00", "【変更】星野商事の保守契約は9月から月額4万円に変更します。対象拠点の追加について先方承認を得ました。"),
("202", "20201", "22", "佐藤", "2024-11-06T09:00:00+09:00", "検索基盤の移行作業です。SQLで閲覧権限を適用してから結果を返します。画面上だけで隠す設計にはしません。"),
("202", "20202", "55", "伊藤", "2025-06-12T15:00:00+09:00", "障害調査：同期処理が停止した間に投稿が100件を超えた可能性があります。復旧後も全履歴が揃っているとは断言せず、再エクスポートで確認します。"),
("202", "20203", "22", "佐藤", "2025-06-13T09:00:00+09:00", "【復旧】API認証を更新しました。検索サービスは再開しましたが、欠損疑いの警告は確認完了まで残します。"),
("202", "20204", "55", "伊藤", "2026-08-10T11:00:00+09:00", "【決定】プロトコルは読取専用MCPに限定します。任意SQL実行やChatworkへの書き込みは公開しません。"),
("303", "30301", "33", "森", "2025-02-10T10:00:00+09:00", "【人事限定】採用計画の予算は120万円です。営業・開発にはこの部屋の閲覧権限を与えていません。"),
("303", "30302", "66", "山本", "2026-07-01T13:00:00+09:00", "採用面談の候補日を7月20日と7月22日で調整します。候補者名や面談内容は全社ルームへ転記しないでください。"),
]

def history_csv() -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["ルームID", "メッセージID", "アカウントID", "発言者", "送信日時", "更新日時", "本文", "状態"])
    for room, mid, sid, name, at, body in CONVERSATIONS:
        writer.writerow([room, mid, sid, name, at, at, body, "active"])
    writer.writerow(["404", "40401", "11", "青木", "2025-01-01T10:00:00+09:00", "", "検索に出してはいけないDMの本文。", "active"])
    writer.writerow(["405", "40501", "11", "青木", "2025-01-01T10:00:00+09:00", "", "検索に出してはいけない個人メモ。", "active"])
    return out.getvalue().encode("utf-8-sig")

def seed(db, reset=False):
    with db.session.begin() as s:
        if s.get(User, ("demo", "aoki")) and not reset:
            return
        if reset:
            for cls in (Message, Membership, SyncState, ImportRun, Inbox, Audit, Room, User):
                s.execute(delete(cls).where(cls.tenant == "demo"))
        for sub, name, account in PEOPLE:
            s.add(User(tenant="demo", subject=sub, name=name, account_id=account, enabled=True))
        for rid, name, kind in ROOMS:
            s.add(Room(tenant="demo", room_id=rid, name=name, kind=kind, enabled=True))
        for sub, ids in MEMBERS.items():
            for rid in ids: s.add(Membership(tenant="demo", subject=sub, room_id=rid, enabled=True))
    Importer(db).import_csv(history_csv(), "demo", timestamp("2026-09-01T04:00:00+09:00"))
    old = "ルームID,アカウントID,発言者,送信日時,本文\n500,11,青木,2023-04-01T10:00:00+09:00,旧資料室の導入相談。メッセージIDがない履歴はスナップショット単位で管理します。\n"
    Importer(db).import_csv(old.encode(), "demo", timestamp("2026-09-01T04:00:00+09:00"), replace_rooms=["500"])
    recent = {"message_id": "10010", "account": {"account_id": 22, "name": "佐藤"},
              "send_time": timestamp("2026-09-11T09:00:00+09:00"), "update_time": 0,
              "body": "本日から検索デモを確認できます。左上の利用者を切り替え、同じ検索語でも見える根拠が変わることを試してください。"}
    Synchronizer(db).apply_recent("demo", "100", [recent], timestamp("2026-09-11T09:01:00+09:00"))

def new_message(db):
    now = int(time.time())
    with db.session() as s:
        count = len(list(s.scalars(select(Message.id).where(Message.tenant == "demo", Message.source == "api"))))
    return Synchronizer(db).apply_recent("demo", "100", [{"message_id": str(900000 + count),
        "account": {"account_id": 22, "name": "佐藤"}, "send_time": now, "update_time": 0,
        "body": f"【新着デモ {count}】同期により新しい発言を保存しました。納期の最新確認は、この発言の時刻と根拠を参照してください。"}])

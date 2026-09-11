"""Chatwork記法の参照を残し、検索用文字列を正規化する。"""
import re
import unicodedata
from datetime import datetime, timezone
from .errors import AppError

def normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()

def references(text: str) -> list[dict]:
    refs = []
    for r, m in re.findall(r"\[rp aid=\d+ to=(\d+)-(\d+)\]", text):
        refs.append({"room_id": r, "message_id": m, "type": "reply"})
    for r, m in re.findall(r"\[qtmeta[^\]]*\brid=(\d+)[^\]]*\bmid=(\d+)[^\]]*\]", text):
        refs.append({"room_id": r, "message_id": m, "type": "quote"})
    return refs

def timestamp(value, name="日時") -> int:
    if isinstance(value, bool):
        raise AppError(422, "invalid_timestamp", f"{name}が不正です")
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdecimal()):
            result = int(value)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError("timezone required")
            result = int(dt.timestamp())
        if not 0 <= result <= 32503680000:
            raise ValueError("range")
        return result
    except (ValueError, TypeError, OverflowError):
        raise AppError(422, "invalid_timestamp", f"{name}にはUnix秒またはタイムゾーン付きISO日時を指定してください") from None

def iso(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()

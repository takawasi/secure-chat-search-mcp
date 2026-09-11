"""ローカルEmbedding。未導入時に疑似ベクトルへ置き換えない。"""
from __future__ import annotations
import math
from threading import Lock
from sqlalchemy import select
from .core import DomainError, Message, Room


class LocalEmbedder:
    def __init__(self, settings):
        self.model_name = settings.embedding_model
        self.cache_dir = settings.model_cache
        self._model = None
        self._lock = Lock()

    def model(self):
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding
                    self._model = TextEmbedding(model_name=self.model_name, cache_dir=self.cache_dir, threads=2)
                except Exception:
                    raise DomainError("embedding_unavailable", "意味検索モデルを準備できません。導入手順とモデルキャッシュを確認してください。", 503) from None
        return self._model

    def passages(self, texts):
        try:
            return [vector.tolist() for vector in self.model().embed(texts)]
        except DomainError:
            raise
        except Exception:
            raise DomainError("embedding_failed", "本文のEmbeddingを作成できません。", 503) from None

    def query(self, text):
        try:
            return next(self.model().query_embed(text)).tolist()
        except DomainError:
            raise
        except Exception:
            raise DomainError("embedding_failed", "質問のEmbeddingを作成できません。", 503) from None


def cosine(a, b):
    if not a or len(a) != len(b) or not all(math.isfinite(float(x)) for x in [*a, *b]):
        raise DomainError("embedding_shape", "Embeddingの形式が一致しません。再索引が必要です。", 503)
    aa, bb = sum(x*x for x in a), sum(x*x for x in b)
    if aa == 0 or bb == 0:
        return 0.0
    return sum(x*y for x, y in zip(a, b)) / math.sqrt(aa*bb)


def index_messages(db, embedder, batch_size=32):
    with db.session() as session:
        rows = list(session.scalars(select(Message).join(Room).where(Room.kind == "group",
            Room.approved.is_(True), Message.deleted.is_(False)).order_by(Message.id)))
        pending = [(m.id, m.body, m.version_at) for m in rows
                   if m.embedding is None or m.embedding_model != embedder.model_name]
    updated = 0
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset+batch_size]
        vectors = embedder.passages([x[1] for x in batch])
        if len(vectors) != len(batch):
            raise DomainError("embedding_count", "Embeddingの件数が一致しません。", 503)
        with db.session() as session:
            for (key, body, version), vector in zip(batch, vectors):
                cosine(vector, vector)
                current = session.get(Message, key)
                room = session.get(Room, current.room_id) if current else None
                if (current and room and room.approved and room.kind == "group" and not current.deleted
                        and current.body == body and current.version_at == version):
                    current.embedding = vector
                    current.embedding_model = embedder.model_name
                    updated += 1
    return {"indexed": updated, "model": embedder.model_name}

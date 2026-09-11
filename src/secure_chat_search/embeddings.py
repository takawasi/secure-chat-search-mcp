"""任意のEmbeddingサービス。既定では一切送信しない。"""
import math
import httpx
from .errors import AppError, UpstreamError

class HttpEmbedder:
    """OpenAI互換の /embeddings 形式。送信先・モデルは利用者が明示する。"""
    def __init__(self, url: str, model: str, key="", transport=None):
        self.url, self.model, self.key, self.transport = url, model, key, transport

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        try:
            with httpx.Client(timeout=25, follow_redirects=False, transport=self.transport) as client:
                for start in range(0, len(texts), 32):
                    batch = texts[start:start + 32]
                    headers = {"Authorization": "Bearer " + self.key} if self.key else {}
                    r = client.post(self.url, json={"model": self.model, "input": batch}, headers=headers)
                    if r.status_code != 200:
                        raise UpstreamError("embedding_unavailable")
                    data = sorted(r.json()["data"], key=lambda x: x["index"])
                    if [x["index"] for x in data] != list(range(len(batch))):
                        raise ValueError("indices")
                    vectors.extend(x["embedding"] for x in data)
            return validate_vectors(vectors, len(texts))
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise UpstreamError("embedding_invalid_response") from None

def validate_vectors(vectors, count):
    if not isinstance(vectors, (list, tuple)) or len(vectors) != count or not vectors:
        raise UpstreamError("embedding_invalid_shape")
    if not isinstance(vectors[0], (list, tuple)):
        raise UpstreamError("embedding_invalid_shape")
    dim = len(vectors[0])
    if not 1 <= dim <= 8192:
        raise UpstreamError("embedding_invalid_shape")
    result = []
    for v in vectors:
        if not isinstance(v, (list, tuple)) or len(v) != dim or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in v):
            raise UpstreamError("embedding_invalid_values")
        norm = math.hypot(*v)
        if not math.isfinite(norm):
            raise UpstreamError("embedding_invalid_values")
        if norm == 0:
            raise UpstreamError("embedding_zero_vector")
        result.append([x/norm for x in v])
    return result

def rrf(rankings: list[list[str]], k=60) -> dict[str, float]:
    scores = {}
    for ranking in rankings:
        for rank, mid in enumerate(dict.fromkeys(ranking), 1):
            scores[mid] = scores.get(mid, 0) + 1 / (k + rank)
    return scores

"""Model-scoped exact-question embedding cache."""

import hashlib
import math
import struct
from typing import Protocol

VECTOR_DIMENSIONS = 512
EMBEDDING_TTL_S = 7 * 24 * 60 * 60


def normalize_question(question: str) -> str:
    return " ".join(question.split()).casefold()


def embedding_cache_key(question: str, model_id: str) -> str:
    value = f"{model_id}\0{normalize_question(question)}".encode()
    return f"emb:{hashlib.sha256(value).hexdigest()}"


class EmbeddingCache(Protocol):
    async def get(self, key: str) -> list[float] | None: ...

    async def put(self, key: str, vector: list[float]) -> None: ...


class RedisEmbeddingCache:
    def __init__(self, client):
        self.client = client

    async def get(self, key: str) -> list[float] | None:
        value = await self.client.get(key)
        if not isinstance(value, bytes) or len(value) != VECTOR_DIMENSIONS * 4:
            return None
        vector = list(struct.unpack(f"<{VECTOR_DIMENSIONS}f", value))
        return vector if all(math.isfinite(number) for number in vector) else None

    async def put(self, key: str, vector: list[float]) -> None:
        if len(vector) != VECTOR_DIMENSIONS or not all(math.isfinite(x) for x in vector):
            raise ValueError("embedding cache requires 512 finite values")
        await self.client.set(
            key, struct.pack(f"<{VECTOR_DIMENSIONS}f", *vector), ex=EMBEDDING_TTL_S
        )

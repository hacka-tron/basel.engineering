"""Exact-question embedding cache with model isolation."""

import pytest

from services.glassbox.cache.embedding import RedisEmbeddingCache, embedding_cache_key


class MemoryClient:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.ttl = ex


def test_key_normalizes_question_and_isolates_model():
    assert embedding_cache_key(" A  question ", "model-a") == embedding_cache_key(
        "a question", "model-a"
    )
    assert embedding_cache_key("a question", "model-a") != embedding_cache_key(
        "a question", "model-b"
    )


@pytest.mark.asyncio
async def test_round_trip_and_corrupt_entry_is_a_miss():
    client = MemoryClient()
    cache = RedisEmbeddingCache(client)
    key = embedding_cache_key("question", "model-a")
    assert await cache.get(key) is None
    await cache.put(key, [0.5] * 512)
    assert await cache.get(key) == [0.5] * 512
    assert client.ttl == 7 * 24 * 60 * 60
    client.values[key] = b"bad"
    assert await cache.get(key) is None

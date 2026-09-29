"""Retrieval and chunk cache contract checks without a server."""

import pytest

from services.glassbox.cache.retrieval import RedisChunkCache, RedisRetrievalCache


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.ttls[key] = ex


@pytest.mark.asyncio
async def test_retrieval_cache_isolated_by_corpus_model_and_version():
    client = MemoryRedis()
    cache = RedisRetrievalCache(client)
    vector = [0.5] * 512
    first = cache.key("about_me", 1, "model-a", vector)
    assert first != cache.key("about_system", 1, "model-a", vector)
    assert first != cache.key("about_me", 2, "model-a", vector)
    assert first != cache.key("about_me", 1, "model-b", vector)
    assert await cache.get(first) is None
    await cache.put(first, [{"chunk_id": 42, "score": 0.8}])
    assert await cache.get(first) == [{"chunk_id": 42, "score": 0.8}]
    assert client.ttls[first] == 3600


@pytest.mark.asyncio
async def test_chunk_cache_preserves_order_and_expires():
    client = MemoryRedis()
    cache = RedisChunkCache(client)
    chunks = [
        {"n": 1, "chunk_id": 8, "text": "first", "source_path": "a"},
        {"n": 2, "chunk_id": 3, "text": "second", "source_path": "b"},
    ]
    matches = [{"chunk_id": 8, "score": 0.9}, {"chunk_id": 3, "score": 0.7}]
    assert await cache.get(matches) is None
    await cache.put(chunks)
    assert await cache.get(matches) == [
        {**chunks[0], "score": 0.9},
        {**chunks[1], "score": 0.7},
    ]
    assert client.ttls["chunktxt:8"] == 86400
    del client.values["chunktxt:3"]
    assert await cache.get(matches) is None

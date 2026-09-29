"""Semantic answer cache against local Redis Stack."""

import asyncio
import json
from uuid import uuid4

import pytest
import redis.asyncio as redis
from fastapi.testclient import TestClient
from sqlalchemy import delete

from services.glassbox.api.main import app
from services.glassbox.cache.answer import RedisAnswerCache
from services.glassbox.cache.embedding import embedding_cache_key
from services.glassbox.db.models import Query
from services.glassbox.db.session import create_db_engine, get_session_factory
from services.glassbox.trace import next_seq


@pytest.mark.asyncio
async def test_semantic_answer_cache_scopes_and_similarity():
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis Stack unavailable: {exc}")
    cache = RedisAnswerCache(client)
    version = uuid4().int % 1_000_000_000
    model_id = f"test-{uuid4().hex}"
    vector = [0.0] * 511 + [1.0]
    payload = {"answer": "A cited answer [1].", "chunks": [{"chunk_id": 42}]}
    try:
        assert await cache.get("about_me", version, model_id, vector) is None
        await cache.put("about_me", version, model_id, vector, payload)
        assert await cache.get("about_me", version, model_id, vector) == payload
        assert await cache.get("about_system", version, model_id, vector) is None
        assert await cache.get("about_me", version + 1, model_id, vector) is None
        assert await cache.get("about_me", version, "different-model", vector) is None
        assert await cache.get("about_me", version, model_id, [1.0] + [0.0] * 511) is None
    finally:
        keys = [key async for key in client.scan_iter(f"ans:about_me:v{version}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


def _events(response):
    return [
        (name.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
        for block in response.text.strip().split("\n\n")
        for name, data in [block.splitlines()]
    ]


def test_repeat_api_request_skips_retrieval_and_llm(monkeypatch):
    from services.glassbox.api import ask
    from services.glassbox.cache import answer as answer_module
    from services.glassbox.providers.fake import FakeLLMProvider

    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_USER", "glassbox")
    monkeypatch.setenv("MYSQL_PASSWORD", "glassbox")
    monkeypatch.setenv("MYSQL_DATABASE", "glassbox")
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"local MySQL unavailable: {exc}")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("GLASSBOX_PROVIDER", "fake")
    get_session_factory.cache_clear()
    calls = {"retrieval": 0, "llm": 0}

    class CountingLLM(FakeLLMProvider):
        async def generate(self, prompt, *, max_tokens):
            calls["llm"] += 1
            async for part in super().generate(prompt, max_tokens=max_tokens):
                yield part

    async def simulated_retrieval(redis_client, **kwargs):
        calls["retrieval"] += 1
        request_id = kwargs["request_id"]
        await redis_client.publish(
            f"trace:{request_id}",
            json.dumps(
                {
                    "type": "retrieval",
                    "request_id": request_id,
                    "seq": await next_seq(redis_client, request_id),
                    "t_ms": 1,
                    "chunks": [
                        {
                            "n": 1,
                            "chunk_id": 42,
                            "text": "Basel builds software.",
                            "source_path": "corpus/about-me/bio.md",
                            "title": "Bio",
                            "score": 0.9,
                        }
                    ],
                }
            ),
        )

    monkeypatch.setattr(ask, "enqueue_retrieval_job", simulated_retrieval)
    monkeypatch.setattr(ask, "get_llm_provider", lambda: CountingLLM())

    class AllowAll:
        async def allow(self, client_hash):
            return True, 0

        async def reserve(self):
            return True

    monkeypatch.setattr(ask, "get_rate_limiter", lambda client: AllowAll())
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: AllowAll())
    question = f"What did Basel build? {uuid4().hex}"
    body = {"question": question, "corpus": "about_me"}
    request_ids = []

    async def current_version():
        client = redis.from_url("redis://127.0.0.1:6379/0")
        try:
            return int(await client.get("corpus:ver:about_me") or 0)
        finally:
            await client.aclose()

    version = asyncio.run(current_version())
    cache_uuid = uuid4()
    monkeypatch.setattr(answer_module, "uuid4", lambda: cache_uuid)
    try:
        http = TestClient(app)
        first = _events(http.post("/api/ask", json=body))
        second = _events(http.post("/api/ask", json=body))
        request_ids = [
            next(data["request_id"] for name, data in events if name == "stage")
            for events in (first, second)
        ]
        assert calls == {"retrieval": 1, "llm": 1}
        assert next(data for name, data in first if name == "done")["answer_cache"] == "miss"
        assert next(data for name, data in second if name == "done")["answer_cache"] == "hit"
        assert all(name != "error" for events in (first, second) for name, _ in events)
        with get_session_factory()() as session:
            rows = session.query(Query).filter(Query.request_id.in_(request_ids)).all()
            assert sorted(row.cache_status for row in rows) == ["answer_hit", "miss"]
    finally:

        async def clean_cache():
            client = redis.from_url("redis://127.0.0.1:6379/0")
            try:
                await client.delete(
                    f"ans:about_me:v{version}:{cache_uuid.hex}",
                    embedding_cache_key(question, "fake-v1"),
                )
            finally:
                await client.aclose()

        asyncio.run(clean_cache())
        if request_ids:
            with engine.begin() as connection:
                connection.execute(delete(Query).where(Query.request_id.in_(request_ids)))
        get_session_factory.cache_clear()
        engine.dispose()

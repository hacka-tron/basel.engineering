"""API stream checks; the publisher simulates the worker's Redis output."""

import asyncio
import json
import os
import struct

import pytest
import redis.asyncio as redis
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from services.glassbox.api.main import app
from services.glassbox.db.models import Chunk, Document, Query
from services.glassbox.db.session import create_db_engine, get_session_factory
from services.glassbox.worker.main import STREAM_NAME

# Integration tests default to the shared local MySQL; point them at a private,
# fully migrated instance with GLASSBOX_TEST_MYSQL_PORT.
TEST_MYSQL_PORT = os.environ.get("GLASSBOX_TEST_MYSQL_PORT", "3306")


def skip_unless_query_log_migrated(engine) -> None:
    """Skip locally (fail in CI) when queries predates migration 0003 (DESIGN-002 §9.3)."""
    with engine.connect() as connection:
        columns = {row[0] for row in connection.exec_driver_sql("SHOW COLUMNS FROM queries")}
    missing = {"turn_index", "rewritten_query"} - columns
    if missing:
        engine.dispose()
        message = f"MySQL queries table not migrated to head (missing {sorted(missing)})"
        # CI migrates to head before pytest, so missing columns there are a real failure.
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)


@pytest.fixture(autouse=True)
def isolated_answer_cache(monkeypatch):
    from services.glassbox.api import ask

    class NoopAnswerCache:
        async def get(self, *args):
            return None

        async def put(self, *args):
            pass

    class AllowAll:
        async def allow(self, client_hash):
            return True, 0

        async def reserve(self):
            return True

    monkeypatch.setattr(ask, "get_answer_cache", lambda client: NoopAnswerCache())
    monkeypatch.setattr(ask, "get_rate_limiter", lambda client: AllowAll())
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: AllowAll())


def events(response):
    return [
        (name.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
        for frame in response.text.strip().split("\n\n")
        for name, data in [frame.splitlines()]
    ]


def test_request_rejects_unknown_corpus():
    response = TestClient(app).post("/api/ask", json={"question": "Who is Basel?", "corpus": "bad"})
    assert response.status_code == 422


class MemoryPubsub:
    def __init__(self, client):
        self.client = client
        self.messages = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def subscribe(self, channel):
        self.client.channel = channel

    async def get_message(self, *, ignore_subscribe_messages, timeout):
        try:
            return await asyncio.wait_for(self.messages.get(), timeout=timeout)
        except TimeoutError:
            return None


class MemoryRedis:
    def __init__(self, outcome="retrieval"):
        self.outcome = outcome
        self.sequence = 0
        self.expirations = []
        self.channel = None
        self.subscription = MemoryPubsub(self)
        self.enqueued = None
        self.cache = {}

    async def get(self, key):
        return self.cache.get(key)

    async def set(self, key, value, *, ex=None, nx=False, px=None):
        if nx and key in self.cache:
            return False
        self.cache[key] = value
        return True

    async def eval(self, script, numkeys, key, token):
        if self.cache.get(key) == token:
            del self.cache[key]
            return 1
        return 0

    async def incr(self, key):
        self.sequence += 1
        return self.sequence

    async def expire(self, key, ttl):
        self.expirations.append((key, ttl))

    def pubsub(self):
        return self.subscription

    async def xadd(self, stream, fields, **kwargs):
        assert self.channel == f"trace:{fields['request_id']}"
        self.enqueued = fields
        if self.outcome == "timeout":
            return
        request_id = fields["request_id"]
        if self.outcome == "retrieval":
            stage_payload = {
                "type": "stage",
                "request_id": request_id,
                "seq": await self.incr(f"seq:{request_id}"),
                "t_ms": 4,
                "node": "vector_search",
                "status": "end",
                "duration_ms": 2,
            }
            await self.expire(f"seq:{request_id}", 300)
            await self.subscription.messages.put({"data": json.dumps(stage_payload).encode()})
        kind = "error" if self.outcome == "error" else "retrieval"
        payload = {
            "type": kind,
            "request_id": request_id,
            "seq": await self.incr(f"seq:{request_id}"),
            "t_ms": 5,
        }
        await self.expire(f"seq:{request_id}", 300)
        if kind == "error":
            payload.update(code="internal", message="worker failed")
        elif self.outcome == "empty":
            payload["chunks"] = []
        else:
            payload["chunks"] = [
                {
                    "n": 1,
                    "chunk_id": 42,
                    "text": "Basel builds software.",
                    "source_path": "about/basel.md",
                    "title": "Basel",
                    "score": 0.8,
                }
            ]
        await self.subscription.messages.put({"data": json.dumps(payload).encode()})

    async def aclose(self):
        pass


def test_stream_handles_worker_message_published_during_enqueue(monkeypatch):
    from services.glassbox.api import ask

    client = MemoryRedis()
    saved = []
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: client)
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: saved.append(kwargs))
    response = TestClient(app).post(
        "/api/ask", json={"question": "What is Basel's background?", "corpus": "about_me"}
    )
    stream = events(response)
    names = [name for name, _ in stream]
    assert names.index("retrieval") < names.index("token") < names.index("done")
    assert names.count("token") > 1
    stages = [data for name, data in stream if name == "stage"]
    assert [stage["seq"] for stage in stages] == sorted(stage["seq"] for stage in stages)
    assert len({stage["seq"] for stage in stages}) == len(stages)
    assert stages[0]["t_ms"] == 0
    assert "text" not in stream[names.index("retrieval")][1]["chunks"][0]
    assert saved[0]["chunks"][0].chunk_id == 42
    assert saved[0]["timings"]["embed"] >= 0
    assert saved[0]["timings"]["vector_search"] == 2
    assert saved[0]["tokens_out"] > 0
    assert len(client.enqueued["embedding"]) == 2048
    assert client.expirations == [(f"seq:{stages[0]['request_id']}", 300)] * client.sequence


def test_second_question_uses_embedding_cache(monkeypatch):
    from services.glassbox.api import ask
    from services.glassbox.providers.fake import FakeEmbeddingProvider

    class CountingProvider(FakeEmbeddingProvider):
        calls = 0

        async def embed(self, texts):
            self.calls += 1
            return await super().embed(texts)

    client = MemoryRedis()
    provider = CountingProvider()
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: client)
    monkeypatch.setattr(ask, "get_embedding_provider", lambda: provider)
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: None)
    http = TestClient(app)
    first = events(http.post("/api/ask", json={"question": "Who is Basel?", "corpus": "about_me"}))
    second = events(
        http.post("/api/ask", json={"question": "  who  is BASEL? ", "corpus": "about_me"})
    )
    assert provider.calls == 1
    assert (
        next(data for name, data in first if name == "stage" and data["node"] == "embed_cache")[
            "cache"
        ]
        == "miss"
    )
    assert (
        next(data for name, data in second if name == "stage" and data["node"] == "embed_cache")[
            "cache"
        ]
        == "hit"
    )
    assert all(data.get("node") != "embed" for name, data in second if name == "stage")


def test_second_question_uses_answer_cache_without_worker_or_llm(monkeypatch):
    from services.glassbox.api import ask
    from services.glassbox.providers.fake import FakeLLMProvider

    class MemoryAnswerCache:
        def __init__(self):
            self.values = {}

        async def get(self, corpus, version, model_id, vector):
            return self.values.get((corpus, version, model_id, struct.pack("512f", *vector)))

        async def put(self, corpus, version, model_id, vector, payload):
            self.values[(corpus, version, model_id, struct.pack("512f", *vector))] = payload

    class CountingLLM(FakeLLMProvider):
        calls = 0

        async def generate(self, prompt, *, max_tokens):
            self.calls += 1
            async for part in super().generate(prompt, max_tokens=max_tokens):
                yield part

    redis_client = MemoryRedis()
    cache = MemoryAnswerCache()
    llm = CountingLLM()
    saved = []

    class OneSlotBudget:
        calls = 0

        async def reserve(self):
            self.calls += 1
            return self.calls <= 2

    budget = OneSlotBudget()
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: redis_client)
    monkeypatch.setattr(ask, "get_answer_cache", lambda client: cache)
    monkeypatch.setattr(ask, "get_llm_provider", lambda: llm)
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: budget)
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: saved.append(kwargs))
    http = TestClient(app)
    body = {"question": "Who is Basel?", "corpus": "about_me"}
    first = events(http.post("/api/ask", json=body))
    enqueued = redis_client.enqueued
    second = events(http.post("/api/ask", json=body))
    assert llm.calls == 1
    assert budget.calls == 1
    assert redis_client.enqueued is enqueued
    assert next(data for name, data in second if name == "done")["answer_cache"] == "hit"
    assert saved[0].get("cache_status", "miss") == "miss"
    assert saved[1]["cache_status"] == "answer_hit"
    assert "".join(data["text"] for name, data in first if name == "token") == "".join(
        data["text"] for name, data in second if name == "token"
    )
    llm.model_id = "fake-llm-v2"
    third = events(http.post("/api/ask", json=body))
    assert llm.calls == 2
    assert budget.calls == 2
    assert next(data for name, data in third if name == "done")["answer_cache"] == "miss"


def test_rate_limited_request_returns_retry_without_retrieval(monkeypatch):
    from services.glassbox.api import ask

    class Deny:
        async def allow(self, client_hash):
            return False, 37

    client = MemoryRedis()
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: client)
    monkeypatch.setattr(ask, "get_rate_limiter", lambda client: Deny())
    stream = events(
        TestClient(app).post("/api/ask", json={"question": "Who is Basel?", "corpus": "about_me"})
    )
    assert stream[-1] == (
        "error",
        {
            "code": "rate_limited",
            "message": "Too many questions. Please try again soon.",
            "retry_after_s": 37,
        },
    )
    assert client.enqueued is None


def test_daily_budget_exhaustion_returns_sources_without_llm(monkeypatch):
    from services.glassbox.api import ask

    class Deny:
        async def reserve(self):
            return False

    client = MemoryRedis()
    saved = []
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: client)
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: Deny())
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: saved.append(kwargs))
    stream = events(
        TestClient(app).post("/api/ask", json={"question": "Who is Basel?", "corpus": "about_me"})
    )
    done = next(data for name, data in stream if name == "done")
    retrieval = next(data for name, data in stream if name == "retrieval")
    assert done["mode"] == "retrieval_only"
    assert retrieval["chunks"][0]["snippet"] == "Basel builds software."
    assert all(name != "token" for name, _ in stream)
    assert saved[0]["mode"] == "retrieval_only"


def test_kill_switch_returns_sources_without_llm_or_budget_reservation(monkeypatch):
    from services.glassbox.api import ask
    from services.glassbox.killswitch import RedisKillSwitch

    class NoBudget:
        async def reserve(self):
            pytest.fail("disabled LLM must not reserve a budget slot")

    client = MemoryRedis()
    client.cache[RedisKillSwitch.KEY] = b"1"
    saved = []
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: client)
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: NoBudget())
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: saved.append(kwargs))
    stream = events(
        TestClient(app).post("/api/ask", json={"question": "Who is Basel?", "corpus": "about_me"})
    )
    done = next(data for name, data in stream if name == "done")
    retrieval = next(data for name, data in stream if name == "retrieval")
    assert done["mode"] == "retrieval_only"
    assert retrieval["chunks"][0]["snippet"] == "Basel builds software."
    assert all(name != "token" for name, _ in stream)
    assert saved[0]["mode"] == "retrieval_only"


def test_empty_model_filtered_retrieval_answers_without_llm(monkeypatch):
    from services.glassbox.api import ask

    class NoBudget:
        async def reserve(self):
            pytest.fail("empty retrieval must not reserve LLM budget")

    class NoLLM:
        model_id = "no-llm"

        async def generate(self, prompt, *, max_tokens):
            pytest.fail("empty retrieval must not invoke the LLM")
            yield ""

    client = MemoryRedis("empty")
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: client)
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: NoBudget())
    monkeypatch.setattr(ask, "get_llm_provider", lambda: NoLLM())
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: None)
    stream = events(
        TestClient(app).post("/api/ask", json={"question": "What exists?", "corpus": "about_me"})
    )
    assert next(data for name, data in stream if name == "retrieval")["chunks"] == []
    assert next(data for name, data in stream if name == "token")["text"] == (
        "I don't know from what I have."
    )
    assert next(data for name, data in stream if name == "done")["mode"] == "full"
    assert all(name != "error" for name, _ in stream)


@pytest.mark.asyncio
async def test_simultaneous_answer_cache_misses_use_one_llm_call(monkeypatch):
    from services.glassbox.api import ask

    class MemoryAnswerCache:
        value = None

        async def get(self, *args):
            return self.value

        async def put(self, *args):
            self.value = args[-1]

    class SlowLLM:
        model_id = "slow-llm"
        calls = 0

        async def generate(self, prompt, *, max_tokens):
            self.calls += 1
            await asyncio.sleep(0.2)
            yield "A grounded answer [1]."

    client = MemoryRedis()
    cache = MemoryAnswerCache()
    llm = SlowLLM()
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: client)
    monkeypatch.setattr(ask, "get_answer_cache", lambda client: cache)
    monkeypatch.setattr(ask, "get_llm_provider", lambda: llm)
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: None)
    body = {"question": "  What   is Basel's work? ", "corpus": "about_me"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        responses = await asyncio.gather(
            http.post("/api/ask", json=body),
            http.post("/api/ask", json={**body, "question": "what is basel's work?"}),
        )
    streams = [events(response) for response in responses]
    assert llm.calls == 1
    assert sorted(
        next(data for name, data in stream if name == "done")["answer_cache"] for stream in streams
    ) == ["hit", "miss"]
    assert all(name != "error" for stream in streams for name, _ in stream)


@pytest.mark.asyncio
async def test_answer_lock_wait_is_bounded(monkeypatch):
    from services.glassbox.api import ask

    class NeverFilled:
        calls = 0

        async def get(self, *args):
            self.calls += 1
            return None

    cache = NeverFilled()
    monkeypatch.setattr(ask, "_ANSWER_LOCK_WAIT_S", 0.03)
    start = asyncio.get_running_loop().time()
    assert await ask._wait_for_answer(cache, "about_me", 1, "model", [0.0] * 512) is None
    assert asyncio.get_running_loop().time() - start < 0.2
    assert cache.calls == 1


def test_worker_error_ends_stream_before_llm(monkeypatch):
    from services.glassbox.api import ask

    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: MemoryRedis("error"))
    stream = events(
        TestClient(app).post("/api/ask", json={"question": "Who is Basel?", "corpus": "about_me"})
    )
    assert stream[-1] == ("error", {"code": "internal", "message": "worker failed"})
    assert all(name not in {"token", "done"} for name, _ in stream)


@pytest.fixture
def integration_stack(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", TEST_MYSQL_PORT)
    monkeypatch.setenv("MYSQL_USER", "glassbox")
    monkeypatch.setenv("MYSQL_PASSWORD", "glassbox")
    monkeypatch.setenv("MYSQL_DATABASE", "glassbox")
    # The long-lived local worker consumes DB 0. Keep this simulated-worker
    # test's real enqueue on a separate Stream so it cannot steal the job.
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/15")
    get_session_factory.cache_clear()
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")
    skip_unless_query_log_migrated(engine)

    async def check_redis():
        client = redis.from_url("redis://127.0.0.1:6379/15")
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        asyncio.run(check_redis())
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")
    yield engine
    get_session_factory.cache_clear()
    engine.dispose()


def test_full_stream_and_query_row_with_simulated_worker(integration_stack, monkeypatch):
    from services.glassbox.api import ask

    with get_session_factory()() as session:
        row = session.execute(
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.corpus == "about_me")
            .limit(1)
        ).first()
    if row is None:
        pytest.skip("Phase 1a about_me chunks are not present")
    chunk, document = row
    original_enqueue = ask.enqueue_retrieval_job
    enqueued = {}

    async def enqueue_and_simulate(client, **kwargs):
        enqueued["message_id"] = await original_enqueue(client, **kwargs)
        enqueued["request_id"] = kwargs["request_id"]

        async def publish():
            # This simulates the worker's output; test_worker.py covers worker behavior.
            await asyncio.sleep(0.02)
            request_id = kwargs["request_id"]
            for node, status, duration in [
                ("vector_search", "start", None),
                ("vector_search", "end", 2),
                ("mysql", "start", None),
                ("mysql", "end", 1),
            ]:
                seq = await client.incr(f"seq:{request_id}")
                await client.expire(f"seq:{request_id}", 300)
                payload = {
                    "type": "stage",
                    "request_id": request_id,
                    "seq": seq,
                    "t_ms": 10,
                    "node": node,
                    "status": status,
                }
                if duration is not None:
                    payload["duration_ms"] = duration
                await client.publish(f"trace:{request_id}", json.dumps(payload))
            await client.publish(
                f"trace:{request_id}",
                json.dumps(
                    {
                        "type": "retrieval",
                        "request_id": request_id,
                        "seq": await client.incr(f"seq:{request_id}"),
                        "t_ms": 12,
                        "chunks": [
                            {
                                "n": 1,
                                "chunk_id": chunk.id,
                                "text": chunk.text,
                                "source_path": document.source_path,
                                "title": document.title or document.source_path,
                                "score": 0.9,
                            }
                        ],
                    }
                ),
            )

        asyncio.create_task(publish())

    monkeypatch.setattr(ask, "enqueue_retrieval_job", enqueue_and_simulate)
    try:
        response = TestClient(app).post(
            "/api/ask", json={"question": "What is Basel's background?", "corpus": "about_me"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        stream = events(response)
        names = [name for name, _ in stream]
        assert names[0] == "stage"
        assert names.index("retrieval") < names.index("token") < names.index("done")
        assert names.count("token") > 1
        stages = [data for name, data in stream if name == "stage"]
        assert [item["seq"] for item in stages] == sorted(item["seq"] for item in stages)
        assert len({item["seq"] for item in stages}) == len(stages)
        assert stages[0]["node"] == "api" and stages[0]["t_ms"] == 0
        assert stream[names.index("retrieval")][1]["chunks"][0]["chunk_id"] == chunk.id
        assert "text" not in stream[names.index("retrieval")][1]["chunks"][0]
        assert stream[-1][1]["mode"] == "full"
        request_id = stages[0]["request_id"]
        with get_session_factory()() as session:
            query = session.scalar(select(Query).where(Query.request_id == request_id))
            assert query is not None
            assert query.chunk_ids == [chunk.id]
            assert query.stage_timings_ms["vector_search"] == 2
            assert query.total_ms >= 0
            assert query.tokens_in > 0 and query.tokens_out > 0
    finally:
        if enqueued:

            async def remove_job():
                client = redis.from_url("redis://127.0.0.1:6379/15")
                try:
                    assert await client.xdel(STREAM_NAME, enqueued["message_id"]) == 1
                finally:
                    await client.aclose()

            try:
                asyncio.run(remove_job())
            finally:
                with get_session_factory()() as session:
                    session.execute(delete(Query).where(Query.request_id == enqueued["request_id"]))
                    session.commit()


def test_retrieval_timeout_emits_error_without_hanging(monkeypatch):
    from services.glassbox.api import ask

    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: MemoryRedis("timeout"))
    monkeypatch.setattr(ask, "RETRIEVAL_TIMEOUT_S", 0.05)
    response = TestClient(app).post(
        "/api/ask", json={"question": "What is Basel's background?", "corpus": "about_me"}
    )
    stream = events(response)
    assert stream[-1][0] == "error"
    assert stream[-1][1]["code"] == "internal"
    assert "retrieval" in stream[-1][1]["message"].lower()
    assert all(name != "done" for name, _ in stream)

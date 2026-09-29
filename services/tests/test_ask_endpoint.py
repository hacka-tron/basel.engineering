"""API stream checks; the publisher simulates the worker's Redis output."""

import asyncio
import json

import pytest
import redis.asyncio as redis
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from services.glassbox.api.main import app
from services.glassbox.db.models import Chunk, Document, Query
from services.glassbox.db.session import create_db_engine, get_session_factory
from services.glassbox.worker.main import STREAM_NAME


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
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_USER", "glassbox")
    monkeypatch.setenv("MYSQL_PASSWORD", "glassbox")
    monkeypatch.setenv("MYSQL_DATABASE", "glassbox")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    get_session_factory.cache_clear()
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")

    async def check_redis():
        client = redis.from_url("redis://127.0.0.1:6379/0")
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
                client = redis.from_url("redis://127.0.0.1:6379/0")
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

"""Integration checks for the Redis retrieval queue and worker."""

import asyncio
import json
import os
import struct
import time
from uuid import uuid4

import pytest
import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from services.glassbox.cache.answer import _model_tag
from services.glassbox.db.models import Chunk, Document
from services.glassbox.db.session import create_db_engine
from services.glassbox.ingest.redis_index import ensure_index, replace_document_vectors
from services.glassbox.retrieval.search import search_chunks
from services.glassbox.worker import main as worker_module
from services.glassbox.worker.main import (
    GROUP_NAME,
    STREAM_NAME,
    enqueue_retrieval_job,
    enqueue_synthetic_jobs,
    ensure_consumer_group,
    process_one_message,
)


@pytest.fixture
def integration_stack(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_USER", "glassbox")
    monkeypatch.setenv("MYSQL_PASSWORD", "glassbox")
    monkeypatch.setenv("MYSQL_DATABASE", "glassbox")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def isolated_stream(monkeypatch):
    # A live development worker may consume retrieval:jobs concurrently.
    stream_name = f"retrieval:jobs:test:{uuid4().hex}"
    monkeypatch.setattr(worker_module, "STREAM_NAME", stream_name)
    return stream_name


async def next_trace_message(pubsub, expected_type):
    for _ in range(10):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if message is None:
            continue
        payload = json.loads(message["data"])
        if payload["type"] == expected_type:
            return payload
    pytest.fail(f"no {expected_type} trace message received")


class RecordingRedis:
    def __init__(self, fields=None):
        self.fields = fields
        self.events = []
        self.acked = []
        self.command = None
        self.enqueued = None
        self.sequence = 41
        self.expirations = []
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    async def set(self, key, value, *, ex):
        self.values[key] = value

    async def execute_command(self, *args):
        self.command = args
        return [1, b"chunk:42", [b"distance", b"0.25"]]

    async def xadd(self, stream, fields, **kwargs):
        self.enqueued = (stream, fields, kwargs)
        return b"1-0"

    async def xreadgroup(self, *args, **kwargs):
        return [(STREAM_NAME.encode(), [(b"1-0", self.fields)])]

    async def publish(self, channel, payload):
        self.events.append((channel, json.loads(payload)))

    async def incr(self, key):
        self.sequence += 1
        return self.sequence

    async def expire(self, key, seconds):
        self.expirations.append((key, seconds))

    async def xack(self, stream, group, message_id):
        self.acked.append((stream, group, message_id))


@pytest.mark.asyncio
async def test_search_uses_existing_index_and_converts_distance_to_similarity():
    client = RecordingRedis()
    matches = await search_chunks(client, [0.25] * 512, "about_me", "fake-v1")
    assert matches == [{"chunk_id": 42, "score": 0.75}]
    assert client.command[:3] == (
        "FT.SEARCH",
        "idx:chunks",
        f"(@corpus:{{about_me}} @model:{{{_model_tag('fake-v1')}}})"
        "=>[KNN 8 @vector $vec AS distance]",
    )
    assert len(client.command[client.command.index("vec") + 1]) == 2048


@pytest.mark.asyncio
async def test_search_isolates_model_during_partial_reingestion():
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis Stack unavailable: {exc}")
    model_a = f"model-a-{uuid4().hex}"
    model_b = f"model-b-{uuid4().hex}"
    ids = [-(uuid4().int % 1_000_000_000) - i for i in range(3)]
    vector = [1.0] + [0.0] * 511
    packed = struct.pack("512f", *vector)
    try:
        await ensure_index(client)
        await replace_document_vectors(
            client, [], [(ids[0], "about_me", packed, "old.md", 1)], model_a
        )
        assert await search_chunks(client, vector, "about_me", model_b) == []
        await replace_document_vectors(
            client,
            [],
            [
                (ids[1], "about_me", packed, "new.md", 2),
                (ids[2], "about_me", packed, "newer.md", 3),
            ],
            model_b,
        )
        assert {
            item["chunk_id"] for item in await search_chunks(client, vector, "about_me", model_b)
        } == set(ids[1:])
        assert {
            item["chunk_id"] for item in await search_chunks(client, vector, "about_me", model_a)
        } == {ids[0]}
    finally:
        await client.delete(*(f"chunk:{chunk_id}" for chunk_id in ids))
        await client.aclose()


@pytest.mark.asyncio
async def test_enqueue_packs_float32_embedding():
    client = RecordingRedis()
    request_start_ts = int(time.time() * 1000)
    message_id = await enqueue_retrieval_job(
        client,
        request_id="test-id",
        question="Why?",
        corpus="about_me",
        embedding=[0.25] * 512,
        request_start_ts=request_start_ts,
    )
    stream, fields, _ = client.enqueued
    assert message_id == b"1-0"
    assert stream == STREAM_NAME
    assert fields["request_id"] == "test-id"
    assert fields["question"] == "Why?"
    assert fields["corpus"] == "about_me"
    assert fields["request_start_ts"] == request_start_ts
    assert struct.unpack("512f", fields["embedding"]) == (0.25,) * 512


@pytest.mark.asyncio
async def test_malformed_job_does_not_crash_and_is_acked_locally():
    client = RecordingRedis(
        {
            b"request_id": b"bad-job",
            b"request_start_ts": str(int(time.time() * 1000)).encode(),
            b"embedding": b"bad",
        }
    )
    assert await process_one_message(client, None, consumer_name="test-worker", block_ms=1)
    assert client.events[0][0] == "trace:bad-job"
    assert client.events[0][1]["type"] == "error"
    assert client.events[0][1]["code"] == "internal"
    assert client.acked == [(STREAM_NAME, GROUP_NAME, b"1-0")]


@pytest.mark.asyncio
async def test_synthetic_job_sleeps_and_never_touches_db_or_publishes(monkeypatch):
    """The stress-test path (DESIGN.md §9.4): no Bedrock/MySQL, no publish."""
    from services.glassbox.worker import main as worker

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    client = RecordingRedis(
        {
            b"request_id": b"synthetic-abc123",
            b"corpus": b"about_me",
            b"synthetic": b"1",
            b"synthetic_delay_ms": b"200",
        }
    )
    # session_factory is None: a synthetic job must never reach MySQL, so
    # touching it at all would raise before the assertions below run.
    assert await process_one_message(client, None, consumer_name="test-worker", block_ms=1)
    assert sleep_calls == [0.2]
    assert client.events == []
    assert client.acked == [(STREAM_NAME, GROUP_NAME, b"1-0")]


@pytest.mark.asyncio
async def test_synthetic_job_defaults_to_200ms_when_delay_missing(monkeypatch):
    from services.glassbox.worker import main as worker

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    client = RecordingRedis({b"request_id": b"synthetic-xyz", b"synthetic": b"1"})
    assert await process_one_message(client, None, consumer_name="test-worker", block_ms=1)
    assert sleep_calls == [0.2]


@pytest.mark.asyncio
async def test_enqueue_synthetic_jobs_flags_every_job(monkeypatch):
    client = RecordingRedis()
    monkeypatch.setattr(worker_module.time, "time", lambda: 1000.0)
    enqueued = []
    original_xadd = client.xadd

    async def recording_xadd(stream, fields, **kwargs):
        enqueued.append(fields)
        return await original_xadd(stream, fields, **kwargs)

    client.xadd = recording_xadd
    count = await enqueue_synthetic_jobs(client, count=5, simulated_delay_ms=150)
    assert count == 5
    assert len(enqueued) == 5
    request_ids = {fields["request_id"] for fields in enqueued}
    assert len(request_ids) == 5  # each job gets a unique request_id
    assert all(rid.startswith("synthetic-") for rid in request_ids)
    assert all(fields["synthetic"] == "1" for fields in enqueued)
    assert all(fields["synthetic_delay_ms"] == 150 for fields in enqueued)
    assert all(fields["embedding_model"] == "synthetic" for fields in enqueued)
    assert all(len(fields["embedding"]) == 2048 for fields in enqueued)


@pytest.mark.asyncio
async def test_enqueue_synthetic_jobs_real_redis_never_publishes(isolated_stream):
    """End-to-end against real local Redis: backlog grows, drains, no publish."""
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis unavailable: {exc}")
    try:
        await ensure_consumer_group(client)
        enqueued = await enqueue_synthetic_jobs(client, count=3, simulated_delay_ms=5)
        assert enqueued == 3
        info = await client.xinfo_groups(isolated_stream)
        assert info[0]["lag"] == 3
        async with client.pubsub() as pubsub:
            await pubsub.psubscribe("trace:synthetic-*")
            for _ in range(3):
                assert await process_one_message(
                    client, None, consumer_name="test-worker", block_ms=100
                )
            # Nothing was ever published for these jobs, unlike a real request.
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3)
            assert message is None
        drained = await client.xinfo_groups(isolated_stream)
        assert drained[0]["lag"] == 0
        assert drained[0]["pending"] == 0
    finally:
        await client.delete(isolated_stream)
        await client.aclose()


@pytest.mark.asyncio
async def test_worker_events_use_redis_sequence_and_request_start(monkeypatch):
    from services.glassbox.worker import main as worker

    async def fake_search(*args, **kwargs):
        return []

    monkeypatch.setattr(worker, "search_chunks", fake_search)
    monkeypatch.setattr(worker, "_load_chunks", lambda session_factory, matches: [])
    monkeypatch.setattr(worker.time, "time", lambda: 1005.0)
    client = RecordingRedis(
        {
            b"request_id": b"shared-request",
            b"request_start_ts": b"1000000",
            b"corpus": b"about_me",
            b"embedding": struct.pack("512f", *([0.25] * 512)),
        }
    )

    assert await process_one_message(client, None, consumer_name="test-worker", block_ms=1)
    events = [payload for _, payload in client.events]
    assert [event["seq"] for event in events] == [42, 43, 44, 45, 46]
    assert all(event["t_ms"] == 5000 for event in events)
    assert client.sequence == 46
    assert client.expirations == [("seq:shared-request", 300)] * len(events)


@pytest.mark.asyncio
async def test_repeat_worker_job_uses_retrieval_and_chunk_caches(monkeypatch):
    calls = {"search": 0, "mysql": 0}

    async def counted_search(*args, **kwargs):
        calls["search"] += 1
        return [{"chunk_id": 42, "score": 0.8}]

    def counted_load(session_factory, matches):
        calls["mysql"] += 1
        return [
            {
                "n": 1,
                "chunk_id": 42,
                "score": 0.8,
                "text": "answer source",
                "source_path": "corpus/about-me/bio.md",
                "title": "Bio",
            }
        ]

    monkeypatch.setattr(worker_module, "search_chunks", counted_search)
    monkeypatch.setattr(worker_module, "_load_chunks", counted_load)
    client = RecordingRedis(
        {
            b"request_id": b"repeat-request",
            b"request_start_ts": b"1000000",
            b"corpus": b"about_me",
            b"embedding_model": b"fake-v1",
            b"embedding": struct.pack("512f", *([0.25] * 512)),
        }
    )
    assert await process_one_message(client, None, consumer_name="test-worker", block_ms=1)
    assert await process_one_message(client, None, consumer_name="test-worker", block_ms=1)
    assert calls == {"search": 1, "mysql": 1}
    second = [payload for _, payload in client.events[-5:]]
    assert [e["cache"] for e in second if e.get("cache")] == ["hit", "hit"]
    assert second[-1]["chunks"][0]["score"] == 0.8


@pytest.mark.asyncio
@pytest.mark.parametrize("hostname", ["retrieval-worker-abc", None])
async def test_run_worker_uses_pod_hostname_or_random_consumer(monkeypatch, hostname):
    from services.glassbox.worker import main as worker

    class Client:
        async def aclose(self):
            pass

    client = Client()
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    if hostname is None:
        monkeypatch.delenv("HOSTNAME", raising=False)
    else:
        monkeypatch.setenv("HOSTNAME", hostname)
    monkeypatch.setattr(worker.redis, "from_url", lambda url: client)

    async def fake_ensure_group(redis_client):
        pass

    monkeypatch.setattr(worker, "ensure_consumer_group", fake_ensure_group)
    monkeypatch.setattr(worker, "get_session_factory", lambda: None)
    seen = []

    async def capture_consumer(redis_client, session_factory, **kwargs):
        seen.append(kwargs.get("consumer_name"))
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "process_one_message", capture_consumer)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_worker()
    assert len(seen) == 1
    if hostname is None:
        assert seen[0].startswith("worker-") and len(seen[0]) == 15
    else:
        assert seen == [hostname]


@pytest.mark.asyncio
async def test_real_job_publishes_chunks_and_is_acked(integration_stack, isolated_stream):
    engine = integration_stack
    client = redis.from_url(os.environ["REDIS_URL"])
    request_id = uuid4().hex
    consumer = f"test-{request_id}"
    request_start_ts = int(time.time() * 1000) - 5000
    with sessionmaker(bind=engine)() as session:
        seeded_chunk = session.scalar(
            select(Chunk).join(Document).where(Document.corpus == "about_me").limit(1)
        )
        if seeded_chunk is None:
            pytest.skip("Phase 1a about_me chunks are not present")
        embedding = list(struct.unpack("512f", seeded_chunk.embedding))
        embedding_model = seeded_chunk.embedding_model
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"real Redis unavailable: {exc}")
    await client.execute_command("FT.INFO", "idx:chunks")
    async with client.pubsub() as pubsub:
        await pubsub.subscribe(f"trace:{request_id}")
        await ensure_consumer_group(client)
        await enqueue_retrieval_job(
            client,
            request_id=request_id,
            question="What is this project about?",
            corpus="about_me",
            embedding=embedding,
            embedding_model=embedding_model,
            request_start_ts=request_start_ts,
        )
        message_id = (await client.xrevrange(isolated_stream, count=1))[0][0]
        try:
            assert await process_one_message(
                client, sessionmaker(bind=engine), consumer_name=consumer, block_ms=100
            )
            events = []
            for _ in range(5):
                for _ in range(10):
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                    if message is not None:
                        events.append(json.loads(message["data"]))
                        break
                else:
                    pytest.fail("no trace message received")
            retrieval = events[-1]
            assert [event["type"] for event in events] == ["stage"] * 4 + ["retrieval"]
            assert [event["seq"] for event in events] == list(
                range(events[0]["seq"], events[0]["seq"] + 5)
            )
            assert int(await client.get(f"seq:{request_id}")) == retrieval["seq"]
            assert 0 < await client.ttl(f"seq:{request_id}") <= 300
            assert all(event["t_ms"] >= 5000 for event in events)
            assert retrieval["chunks"]
            assert retrieval["chunks"][0]["chunk_id"] == seeded_chunk.id
            assert retrieval["chunks"][0]["text"]
            assert retrieval["chunks"][0]["source_path"]
            assert retrieval["chunks"][0]["title"]
            assert retrieval["chunks"][0]["n"] == 1
            assert (
                await client.xpending_range(isolated_stream, GROUP_NAME, message_id, message_id, 1)
                == []
            )
        finally:
            await client.delete(isolated_stream)
            await client.aclose()


@pytest.mark.asyncio
async def test_bad_embedding_publishes_error_and_is_acked(integration_stack, isolated_stream):
    engine = integration_stack
    client = redis.from_url(os.environ["REDIS_URL"])
    request_id = uuid4().hex
    async with client.pubsub() as pubsub:
        await pubsub.subscribe(f"trace:{request_id}")
        await ensure_consumer_group(client)
        message_id = await client.xadd(
            isolated_stream,
            {
                "request_id": request_id,
                "question": "Bad vector",
                "corpus": "about_me",
                "request_start_ts": int(time.time() * 1000),
                "embedding": b"bad",
            },
        )
        try:
            assert await process_one_message(
                client,
                sessionmaker(bind=engine),
                consumer_name=f"test-{request_id}",
                block_ms=100,
            )
            error = await next_trace_message(pubsub, "error")
            assert error["code"] == "internal"
            assert "embedding" in error["message"]
            assert (
                await client.xpending_range(isolated_stream, GROUP_NAME, message_id, message_id, 1)
                == []
            )
        finally:
            await client.delete(isolated_stream)
            await client.aclose()

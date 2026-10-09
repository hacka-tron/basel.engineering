"""Semantic answer cache: source validation (in-memory) and Redis Stack coverage."""

import asyncio
import json
import math
import re
import struct
from uuid import uuid4

import pytest
import redis.asyncio as redis
from fastapi.testclient import TestClient
from sqlalchemy import delete

from services.glassbox.api import ask as ask_module
from services.glassbox.api.main import app
from services.glassbox.cache import answer as answer_module
from services.glassbox.cache.answer import (
    INDEX_NAME,
    KEY_PREFIX,
    LEGACY_INDEX_NAME,
    RedisAnswerCache,
    _model_tag,
    chunk_content_sha,
    drop_legacy_index,
)
from services.glassbox.cache.embedding import embedding_cache_key
from services.glassbox.db.models import Query
from services.glassbox.db.session import create_db_engine, get_session_factory
from services.glassbox.ingest.redis_index import replace_document_vectors
from services.glassbox.retrieval.search import RETRIEVAL_MODE
from services.glassbox.trace import next_seq
from services.tests.stack_ports import redis_url_for
from services.tests.test_ask_endpoint import TEST_MYSQL_PORT, skip_unless_query_log_migrated

MODEL = "fake-v1|fake-llm|v13"
V1 = [1.0] + [0.0] * 511
V2 = [0.0, 1.0] + [0.0] * 510


class _Pipeline:
    def __init__(self, store):
        self.store = store
        self.ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.ops.append((name, args, kwargs))

    async def execute(self):
        return [await getattr(self.store, name)(*args, **kwargs) for name, args, kwargs in self.ops]


class SearchRedis:
    """Enough of redis.asyncio + Redis Search for the answer cache and chunk keys.

    FT.SEARCH is a brute-force cosine KNN over the hashes under the index prefix,
    filtered by the corpus and model tags in the query, like the real index.
    """

    def __init__(self):
        self.hashes: dict[str, dict] = {}
        self.strings: dict[str, bytes] = {}
        self.indexes: dict[str, str] = {}

    async def execute_command(self, command, *args):
        if command == "FT.INFO":
            if args[0] not in self.indexes:
                from redis.exceptions import ResponseError

                raise ResponseError("Unknown index name")
            return []
        if command == "FT.CREATE":
            self.indexes[args[0]] = args[args.index("PREFIX") + 2]
            return b"OK"
        if command == "FT.DROPINDEX":
            assert args == (args[0],), "never DD: the hashes must stay"
            if self.indexes.pop(args[0], None) is None:
                from redis.exceptions import ResponseError

                raise ResponseError("Unknown Index name")
            return b"OK"
        assert command == "FT.SEARCH"
        index, query = args[0], args[1]
        prefix = self.indexes[index]
        vector = struct.unpack("<512f", args[args.index("vec") + 1])
        k = int(re.search(r"KNN (\d+)", query).group(1))
        tags = dict(re.findall(r"@(\w+):\{([^}]*)\}", query))
        rows = []
        for key, fields in self.hashes.items():
            if not key.startswith(prefix):
                continue
            if any(fields.get(tag) != value for tag, value in tags.items()):
                continue
            stored = struct.unpack("<512f", fields["vector"])
            dot = sum(a * b for a, b in zip(vector, stored, strict=True))
            norm = math.sqrt(sum(a * a for a in vector)) * math.sqrt(sum(b * b for b in stored))
            rows.append((1 - dot / norm, key))
        rows.sort()
        result = [len(rows[:k])]
        for distance, key in rows[:k]:
            result += [key.encode(), [b"distance", str(distance).encode()]]
        return result

    def pipeline(self, transaction=True):
        return _Pipeline(self)

    async def hset(self, key, field=None, value=None, mapping=None):
        target = self.hashes.setdefault(key, {})
        target.update(mapping or {field: value})

    async def hget(self, key, field):
        key = key.decode() if isinstance(key, bytes) else key
        value = self.hashes.get(key, {}).get(field)
        return value.encode() if isinstance(value, str) else value

    async def expire(self, key, ttl):
        return True

    async def exists(self, *keys):
        return sum(key in self.hashes or key in self.strings for key in keys)

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            key = key.decode() if isinstance(key, bytes) else key
            removed += self.hashes.pop(key, None) is not None
            removed += self.strings.pop(key, None) is not None
        return removed

    async def incr(self, key):
        value = int(self.strings.get(key, b"0")) + 1
        self.strings[key] = str(value).encode()
        return value

    async def get(self, key):
        return self.strings.get(key)


def _text(chunk_id, revision=0):
    return f"Chunk {chunk_id} text, revision {revision}."


def _payload(*chunk_ids, answer="Basel builds software."):
    return {
        "answer": answer,
        "chunks": [
            {
                "n": n,
                "chunk_id": chunk_id,
                "text": _text(chunk_id),
                "source_path": f"docs/doc{chunk_id}.md",
            }
            for n, chunk_id in enumerate(chunk_ids, start=1)
        ],
    }


def _stored(payload):
    """What the cache stores and replays: the payload plus its source hashes."""
    return {
        **payload,
        "sources": {
            str(chunk["chunk_id"]): chunk_content_sha(chunk["text"]) for chunk in payload["chunks"]
        },
    }


async def _ingest_document(
    client, old_ids, new_ids, source_path, document_id, revision=0, text=None
):
    """What ingest does to Redis for a new or changed document (plus the version bump)."""
    await replace_document_vectors(
        client,
        old_ids,
        [
            (
                chunk_id,
                "about_system",
                struct.pack("512f", *V2),
                source_path,
                document_id,
                text if text is not None else _text(chunk_id, revision),
            )
            for chunk_id in new_ids
        ],
        "fake-v1",
    )
    await client.incr("corpus:ver:about_system")


@pytest.mark.asyncio
async def test_hit_survives_a_reingest_that_changed_an_unrelated_document():
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [1, 2], "docs/source.md", 10)
    await _ingest_document(client, [], [3], "docs/other.md", 11)
    payload = _payload(1, 2)
    await cache.put("about_system", MODEL, V1, payload)
    # Another document changes: new chunk ids for it, and the corpus version moves.
    await _ingest_document(client, [3], [4], "docs/other.md", 11)
    assert await cache.get("about_system", MODEL, V1) == _stored(payload)


@pytest.mark.asyncio
async def test_miss_after_a_source_document_changes():
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [1, 2], "docs/source.md", 10)
    await cache.put("about_system", MODEL, V1, _payload(1))
    # The answer used chunk 1 only, but ingest replaces every chunk of a changed doc.
    await _ingest_document(client, [1, 2], [5, 6], "docs/source.md", 10)
    assert await cache.get("about_system", MODEL, V1) is None
    # The stale entry is deleted on sight, not re-checked on every read.
    assert not [key for key in client.hashes if key.startswith(KEY_PREFIX)]


@pytest.mark.asyncio
async def test_miss_after_a_source_document_is_deleted():
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [1], "docs/kept.md", 10)
    await _ingest_document(client, [], [2], "docs/gone.md", 11)
    await cache.put("about_system", MODEL, V1, _payload(1, 2))
    # The #101 stale sweep: delete the document's chunk keys, then bump the version.
    await client.delete("chunk:2")
    await client.incr("corpus:ver:about_system")
    assert await cache.get("about_system", MODEL, V1) is None


@pytest.mark.asyncio
async def test_miss_when_a_reused_chunk_id_now_holds_different_text():
    """A MySQL wipe/TRUNCATE/restore restarts AUTO_INCREMENT while Redis keeps its keys."""
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [5], "docs/source.md", 10)
    await cache.put("about_system", MODEL, V1, _payload(5))
    # A fresh ingest after the wipe writes chunk:5 again, for different text.
    await _ingest_document(client, [], [5], "docs/unrelated.md", 1, revision=1)
    assert await client.exists("chunk:5") == 1
    assert await cache.get("about_system", MODEL, V1) is None


@pytest.mark.asyncio
async def test_miss_when_a_chunk_hash_predates_content_sha():
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [1], "docs/source.md", 10)
    await cache.put("about_system", MODEL, V1, _payload(1))
    del client.hashes["chunk:1"]["content_sha"]
    assert await cache.get("about_system", MODEL, V1) is None


@pytest.mark.asyncio
async def test_unverifiable_answers_are_not_written():
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [1], "docs/source.md", 10)
    no_text = {"answer": "An answer.", "chunks": [{"n": 1, "chunk_id": 1}]}
    await cache.put("about_system", MODEL, V1, no_text)
    edited = _payload(1)
    edited["chunks"][0]["text"] = "Text the index no longer holds."
    await cache.put("about_system", MODEL, V1, edited)
    assert not [key for key in client.hashes if key.startswith(KEY_PREFIX)]


@pytest.mark.asyncio
async def test_a_stale_nearest_entry_does_not_hide_a_valid_one():
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [1], "docs/source.md", 10)
    await cache.put("about_system", MODEL, V1, _payload(1, answer="Old answer."))
    await _ingest_document(client, [1], [2], "docs/source.md", 10)
    fresh = _payload(2, answer="New answer.")
    await cache.put("about_system", MODEL, V1, fresh)
    assert await cache.get("about_system", MODEL, V1) == _stored(fresh)


@pytest.mark.asyncio
async def test_write_is_skipped_when_a_source_vanished_during_generation():
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [1], "docs/source.md", 10)
    await cache.put("about_system", MODEL, V1, _payload(1, 99))
    assert not [key for key in client.hashes if key.startswith(KEY_PREFIX)]


@pytest.mark.asyncio
async def test_entry_is_scoped_by_corpus_model_and_similarity_but_not_corpus_version():
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [1], "docs/source.md", 10)
    payload = _payload(1)
    await cache.put("about_system", MODEL, V1, payload)
    stored = next(fields for key, fields in client.hashes.items() if key.startswith(KEY_PREFIX))
    assert set(stored) == {"corpus", "model", "vector", "payload"}
    for _ in range(5):
        await client.incr("corpus:ver:about_system")
    assert await cache.get("about_system", MODEL, V1) == _stored(payload)
    assert await cache.get("about_me", MODEL, V1) is None
    assert await cache.get("about_system", "fake-v1|fake-llm|v14", V1) is None
    assert await cache.get("about_system", MODEL, V2) is None


@pytest.mark.asyncio
async def test_legacy_entries_are_never_read():
    client = SearchRedis()
    cache = RedisAnswerCache(client)
    await _ingest_document(client, [], [1], "docs/source.md", 10)
    # A pre-source-validation entry: the old index, the old key prefix, a version tag.
    client.indexes["idx:answers"] = "ans:"
    client.hashes["ans:about_system:v7:legacy"] = {
        "corpus": "about_system",
        "version": "7",
        "model": _model_tag(MODEL),
        "vector": struct.pack("<512f", *V1),
        "payload": json.dumps(_stored(_payload(1))),
    }
    assert await cache.get("about_system", MODEL, V1) is None
    assert INDEX_NAME != "idx:answers" and not KEY_PREFIX.startswith("ans:")
    # An entry that names no source chunks can never validate either.
    client.hashes[f"{KEY_PREFIX}about_system:nosources"] = {
        "corpus": "about_system",
        "model": _model_tag(MODEL),
        "vector": struct.pack("<512f", *V1),
        "payload": json.dumps(_payload(1)),  # valid chunks, but no "sources" map
    }
    assert await cache.get("about_system", MODEL, V1) is None


def test_answer_lock_key_has_no_corpus_version():
    key = ask_module._answer_lock_key("about_me", MODEL, "  Who is   Basel? ")
    assert key == ask_module._answer_lock_key("about_me", MODEL, "who is basel?")
    assert key != ask_module._answer_lock_key("about_me", "other-model", "who is basel?")
    assert key != ask_module._answer_lock_key("about_system", MODEL, "who is basel?")


def test_warm_up_still_hits_after_an_unrelated_reingest(monkeypatch):
    """The warm-up job's cache hits survive an unrelated docs edit (the #warm path)."""
    from services.glassbox import warm
    from services.glassbox.providers.fake import FakeLLMProvider
    from services.tests.test_ask_endpoint import MemoryRedis

    class CountingLLM(FakeLLMProvider):
        calls = 0

        async def generate(self, prompt, *, max_tokens, system=None):
            CountingLLM.calls += 1
            async for part in super().generate(prompt, max_tokens=max_tokens):
                yield part

    class AllowAll:
        async def allow(self, client_hash):
            return True, 0

        async def reserve(self):
            return True

    search = SearchRedis()
    # MemoryRedis's simulated worker always retrieves chunk 42.
    asyncio.run(
        _ingest_document(search, [], [42], "about/basel.md", 1, text="Basel builds software.")
    )
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setenv("GLASSBOX_PROVIDER", "fake")
    monkeypatch.setattr(ask_module.redis, "from_url", lambda url: MemoryRedis())
    monkeypatch.setattr(ask_module, "get_answer_cache", lambda client: RedisAnswerCache(search))
    monkeypatch.setattr(ask_module, "get_llm_provider", lambda: CountingLLM())
    monkeypatch.setattr(ask_module, "get_rate_limiter", lambda client: AllowAll())
    monkeypatch.setattr(ask_module, "get_daily_budget", lambda client: AllowAll())
    monkeypatch.setattr(ask_module, "_save_query", lambda **kwargs: None)
    http = TestClient(app)

    def ask_fn(api_url, corpus, question):
        response = http.post("/api/ask", json={"question": question, "corpus": corpus})
        return warm.classify(corpus, question, warm.parse_sse(response.content.split(b"\n")))

    def run():
        outcomes, stopped = warm.warm(
            "http://test", [("about_me", "Who is Basel?")], max_llm_calls=1, ask_fn=ask_fn
        )
        assert stopped is None
        return outcomes[0].result

    assert run() == "warmed"
    asyncio.run(_ingest_document(search, [], [43], "about/other.md", 2))
    assert run() == "cached"
    assert CountingLLM.calls == 1
    # Its own source changes: the next warm-up regenerates.
    asyncio.run(_ingest_document(search, [42], [44], "about/basel.md", 1))
    assert run() == "warmed"
    assert CountingLLM.calls == 2


@pytest.mark.asyncio
async def test_drop_legacy_index_keeps_the_keys_and_is_idempotent():
    client = SearchRedis()
    client.indexes[LEGACY_INDEX_NAME] = "ans:"
    client.indexes[INDEX_NAME] = KEY_PREFIX
    client.hashes["ans:about_system:v7:legacy"] = {"corpus": "about_system"}
    assert await drop_legacy_index(client) is True
    assert LEGACY_INDEX_NAME not in client.indexes
    assert INDEX_NAME in client.indexes  # the live v2 index is untouched
    assert "ans:about_system:v7:legacy" in client.hashes  # left to expire by TTL
    assert await drop_legacy_index(client) is False


@pytest.mark.asyncio
async def test_drop_legacy_index_raises_other_errors():
    from redis.exceptions import ResponseError

    class Broken:
        async def execute_command(self, *args):
            raise ResponseError("LOADING Redis is loading the dataset in memory")

    with pytest.raises(ResponseError):
        await drop_legacy_index(Broken())


@pytest.mark.asyncio
async def test_new_chunk_keys_drop_their_cached_text():
    """Ids restart after a MySQL wipe: a rewritten chunk:{id} must not keep old chunktxt."""
    client = SearchRedis()
    client.strings["chunktxt:1"] = b'{"text": "old text of a reused id"}'
    client.strings["chunktxt:7"] = b'{"text": "a replaced chunk"}'
    client.strings["chunktxt:9"] = b'{"text": "an unrelated chunk"}'
    await _ingest_document(client, [7], [1], "docs/source.md", 10)
    assert "chunktxt:1" not in client.strings
    assert "chunktxt:7" not in client.strings and "chunk:7" not in client.hashes
    assert "chunktxt:9" in client.strings
    assert "chunk:1" in client.hashes


async def _redis_or_skip():
    client = redis.from_url(redis_url_for())
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis Stack unavailable: {exc}")
    return client


@pytest.mark.asyncio
async def test_drop_legacy_index_against_redis_stack():
    """FT.DROPINDEX without DD keeps the hashes; the missing-index error is recognised."""
    client = await _redis_or_skip()
    prefix = f"ans:droptest-{uuid4().hex}:"
    key = f"{prefix}entry"
    try:
        await drop_legacy_index(client)  # a leftover dev index, if any
        await client.execute_command(
            "FT.CREATE", LEGACY_INDEX_NAME, "ON", "HASH", "PREFIX", "1", prefix,
            "SCHEMA", "corpus", "TAG",
        )  # fmt: skip
        await client.hset(key, mapping={"corpus": "about_me"})
        assert await drop_legacy_index(client) is True
        assert await client.exists(key) == 1
        assert await drop_legacy_index(client) is False
    finally:
        await client.delete(key)
        await client.aclose()


@pytest.mark.asyncio
async def test_semantic_answer_cache_against_redis_stack():
    client = await _redis_or_skip()
    cache = RedisAnswerCache(client)
    model_id = f"test-{uuid4().hex}"
    # Chunk ids far above any real auto-increment value; tagged with a corpus no
    # search uses, and without a vector, so retrieval never sees them.
    chunk_id = 10**15 + uuid4().int % 10**9
    chunk_key = f"chunk:{chunk_id}"
    legacy_key = f"ans:about_me:v1:{uuid4().hex}"
    payload = {"answer": "A grounded answer.", "chunks": [{"chunk_id": chunk_id, "text": "T."}]}
    stored = {**payload, "sources": {str(chunk_id): chunk_content_sha("T.")}}
    chunk_fields = {"corpus": "answer_cache_test", "content_sha": chunk_content_sha("T.")}
    try:
        await client.hset(chunk_key, mapping=chunk_fields)
        assert await cache.get("about_me", model_id, V1) is None
        await cache.put("about_me", model_id, V1, payload)
        assert await cache.get("about_me", model_id, V1) == stored
        await client.incr("corpus:ver:answer_cache_test")
        assert await cache.get("about_me", model_id, V1) == stored
        assert await cache.get("about_system", model_id, V1) is None
        assert await cache.get("about_me", "different-model", V1) is None
        assert await cache.get("about_me", model_id, V2) is None
        # Same id, different text (a reused id after a MySQL wipe): a miss.
        await client.hset(chunk_key, "content_sha", chunk_content_sha("Other text."))
        assert await cache.get("about_me", model_id, V1) is None
        await client.hset(chunk_key, mapping=chunk_fields)
        await cache.put("about_me", model_id, V1, payload)
        assert await cache.get("about_me", model_id, V1) == stored
        await client.delete(chunk_key)
        assert await cache.get("about_me", model_id, V1) is None
        assert not [
            key
            async for key in client.scan_iter(f"{KEY_PREFIX}about_me:*")
            if await client.hget(key, "model") == _model_tag(model_id).encode()
        ]
        # A legacy-format entry is invisible to the new index.
        await client.hset(chunk_key, mapping=chunk_fields)
        await client.hset(
            legacy_key,
            mapping={
                "corpus": "about_me",
                "version": "1",
                "model": _model_tag(model_id),
                "vector": struct.pack("<512f", *V1),
                "payload": json.dumps(stored),
            },
        )
        assert await cache.get("about_me", model_id, V1) is None
    finally:
        await client.delete(chunk_key, legacy_key, "corpus:ver:answer_cache_test")
        async for key in client.scan_iter(f"{KEY_PREFIX}about_me:*"):
            if await client.hget(key, "model") == _model_tag(model_id).encode():
                await client.delete(key)
        await client.aclose()


def _events(response):
    return [
        (name.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
        for block in response.text.strip().split("\n\n")
        for name, data in [block.splitlines()]
    ]


def test_repeat_api_request_skips_retrieval_and_llm(monkeypatch):
    from services.glassbox.api import ask
    from services.glassbox.providers.fake import FakeLLMProvider

    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", TEST_MYSQL_PORT)
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
    skip_unless_query_log_migrated(engine)
    monkeypatch.setenv("REDIS_URL", redis_url_for())
    monkeypatch.setenv("GLASSBOX_PROVIDER", "fake")
    get_session_factory.cache_clear()
    calls = {"retrieval": 0, "llm": 0}
    # The cache serves an answer only while its source chunks are indexed.
    chunk_id = 10**15 + uuid4().int % 10**9

    class CountingLLM(FakeLLMProvider):
        async def generate(self, prompt, *, max_tokens, system=None):
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
                    "retrieval_mode": RETRIEVAL_MODE,
                    "request_id": request_id,
                    "seq": await next_seq(redis_client, request_id),
                    "t_ms": 1,
                    "chunks": [
                        {
                            "n": 1,
                            "chunk_id": chunk_id,
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

    async def index_source_chunk():
        client = redis.from_url(redis_url_for())
        try:
            await client.hset(
                f"chunk:{chunk_id}",
                mapping={
                    "corpus": "answer_cache_test",
                    "content_sha": chunk_content_sha("Basel builds software."),
                },
            )
        finally:
            await client.aclose()

    asyncio.run(index_source_chunk())
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
            client = redis.from_url(redis_url_for())
            try:
                await client.delete(
                    f"{KEY_PREFIX}about_me:{cache_uuid.hex}",
                    f"chunk:{chunk_id}",
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

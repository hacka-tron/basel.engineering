"""Consume retrieval jobs and publish trace events to Redis pub/sub."""

import asyncio
import json
import logging
import os
import struct
import time
from pathlib import Path
from uuid import uuid4

import redis.asyncio as redis
from redis.exceptions import ResponseError
from sqlalchemy import select

from services.glassbox.cache.retrieval import (
    ChunkCache,
    RedisChunkCache,
    RedisRetrievalCache,
    RetrievalCache,
)
from services.glassbox.corpora import search_corpora
from services.glassbox.db.models import Chunk, Document
from services.glassbox.db.session import get_session_factory
from services.glassbox.retrieval.search import (
    RETRIEVAL_MODE,
    VECTOR_DIMENSIONS,
    hybrid_search,
    lexical_terms,
    tech_question_terms,
)
from services.glassbox.trace import elapsed_ms, next_seq

STREAM_NAME = "retrieval:jobs"
GROUP_NAME = "workers"
LOGGER = logging.getLogger(__name__)
# The only text a failed retrieval job publishes (see process_one_message).
RETRIEVAL_FAILED_MESSAGE = "Retrieval failed"


async def search_version(retrieval_cache: RetrievalCache, corpus: str) -> int:
    """The retrieval-cache version for a topic: the sum over the corpora it searches.

    About Basel also searches the portfolio files, so an ingest of either corpus must
    retire its cached results. Each ``corpus:ver`` counter only grows, so the sum
    does too and never returns to a value an older entry was written under.
    """
    return sum([await retrieval_cache.version(name) for name in search_corpora(corpus)])


async def ensure_consumer_group(redis_client) -> None:
    try:
        await redis_client.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def enqueue_retrieval_job(
    redis_client,
    *,
    request_id: str,
    question: str,
    corpus: str,
    embedding: list[float],
    request_start_ts: int,
    embedding_model: str = "unknown",
) -> bytes:
    if len(embedding) != VECTOR_DIMENSIONS:
        raise ValueError(f"embedding must have {VECTOR_DIMENSIONS} float32 values")
    return await redis_client.xadd(
        STREAM_NAME,
        {
            "request_id": request_id,
            "question": question,
            "corpus": corpus,
            "request_start_ts": request_start_ts,
            "embedding": struct.pack(f"{VECTOR_DIMENSIONS}f", *embedding),
            "embedding_model": embedding_model,
        },
        maxlen=10000,
        approximate=True,
    )


_SYNTHETIC_EMBEDDING = struct.pack(f"{VECTOR_DIMENSIONS}f", *([0.0] * VECTOR_DIMENSIONS))


async def enqueue_synthetic_jobs(
    redis_client,
    *,
    count: int = 300,
    simulated_delay_ms: int = 200,
) -> int:
    """Build retrieval-queue backlog for the stress-test demo (DESIGN.md §9.4).

    Synthetic jobs never call an embedding or LLM provider — the embedding is
    a fixed placeholder vector, never looked up or computed — and
    `process_one_message` recognizes `synthetic=1` and does a fixed simulated
    delay instead of any real retrieval/Bedrock work. Their only purpose is to
    grow `retrieval:jobs`' consumer-group lag so KEDA's ScaledObject scales
    `retrieval-worker` up; nothing about them is ever published anywhere a
    real client could observe it (see the synthetic branch below).
    """
    now_ts = int(time.time() * 1000)
    enqueued = 0
    for _ in range(count):
        await redis_client.xadd(
            STREAM_NAME,
            {
                "request_id": f"synthetic-{uuid4().hex}",
                "corpus": "about_me",
                "request_start_ts": now_ts,
                "embedding": _SYNTHETIC_EMBEDDING,
                "embedding_model": "synthetic",
                "synthetic": "1",
                "synthetic_delay_ms": simulated_delay_ms,
            },
            maxlen=10000,
            approximate=True,
        )
        enqueued += 1
    return enqueued


def _field(fields: dict, name: str):
    return fields.get(name.encode(), fields.get(name))


async def _publish(redis_client, channel: str, event_type: str, data: dict) -> None:
    await redis_client.publish(channel, json.dumps({"type": event_type, **data}))


def _load_chunks(session_factory, matches: list[dict]) -> list[dict]:
    if not matches:
        return []
    ids = [match["chunk_id"] for match in matches]
    with session_factory() as session:
        rows = session.execute(
            select(
                Chunk.id,
                Chunk.text,
                Chunk.start_line,
                Chunk.end_line,
                Document.source_path,
                Document.title,
            )
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.id.in_(ids))
        ).all()
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(ids):
        raise ValueError("one or more matched chunks are missing from MySQL")
    return [
        {
            "n": rank,
            "chunk_id": match["chunk_id"],
            "text": by_id[match["chunk_id"]].text,
            "source_path": by_id[match["chunk_id"]].source_path,
            "title": by_id[match["chunk_id"]].title
            or Path(by_id[match["chunk_id"]].source_path).name,
            "score": match["score"],
            "start_line": by_id[match["chunk_id"]].start_line,
            "end_line": by_id[match["chunk_id"]].end_line,
        }
        for rank, match in enumerate(matches, start=1)
    ]


async def process_one_message(
    redis_client, session_factory, *, consumer_name: str, block_ms: int = 3000
) -> bool:
    """Read and settle one new job; return False when the block timeout expires."""
    streams = await redis_client.xreadgroup(
        GROUP_NAME, consumer_name, {STREAM_NAME: ">"}, count=1, block=block_ms
    )
    if not streams:
        return False
    _, messages = streams[0]
    message_id, fields = messages[0]

    synthetic = _field(fields, "synthetic")
    if isinstance(synthetic, bytes):
        synthetic = synthetic.decode()
    if synthetic == "1":
        # Stress-test job (see enqueue_synthetic_jobs): simulate work with a
        # fixed delay only. No embedding/LLM/Bedrock call, no MySQL lookup,
        # and no publish to trace:{request_id} — nothing subscribes to a
        # synthetic request's channel, and this keeps it that way even if a
        # real request_id were ever accidentally reused (it can't be here,
        # since synthetic IDs use their own "synthetic-" prefix).
        delay_raw = _field(fields, "synthetic_delay_ms") or b"200"
        delay_ms = int(delay_raw.decode() if isinstance(delay_raw, bytes) else delay_raw)
        try:
            await asyncio.sleep(delay_ms / 1000)
        finally:
            await redis_client.xack(STREAM_NAME, GROUP_NAME, message_id)
        return True

    request_id = _field(fields, "request_id")
    if isinstance(request_id, bytes):
        request_id = request_id.decode(errors="replace")
    channel = f"trace:{request_id}" if request_id else None

    request_start_ts = None

    async def emit(event_type: str, data: dict) -> None:
        seq = await next_seq(redis_client, request_id)
        t_ms = elapsed_ms(request_start_ts) if request_start_ts is not None else 0
        await _publish(
            redis_client,
            channel,
            event_type,
            {
                "request_id": request_id,
                "seq": seq,
                "t_ms": t_ms,
                **data,
            },
        )

    async def stage(
        node: str, status: str, duration_ms: int | None = None, cache: str | None = None
    ) -> None:
        payload = {"node": node, "status": status}
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if cache is not None:
            payload["cache"] = cache
        await emit("stage", payload)

    try:
        if not request_id:
            raise ValueError("job is missing request_id")
        raw_request_start_ts = _field(fields, "request_start_ts")
        if raw_request_start_ts is None:
            raise ValueError("job is missing request_start_ts")
        request_start_ts = int(raw_request_start_ts)
        corpus = _field(fields, "corpus")
        if isinstance(corpus, bytes):
            corpus = corpus.decode()
        packed = _field(fields, "embedding")
        if not isinstance(packed, bytes) or len(packed) != VECTOR_DIMENSIONS * 4:
            raise ValueError("embedding must contain 512 packed float32 values")
        embedding = list(struct.unpack(f"{VECTOR_DIMENSIONS}f", packed))
        embedding_model = _field(fields, "embedding_model") or b"unknown"
        if isinstance(embedding_model, bytes):
            embedding_model = embedding_model.decode()
        retrieval_cache: RetrievalCache = RedisRetrievalCache(redis_client)
        chunk_cache: ChunkCache = RedisChunkCache(redis_client)
        version = await search_version(retrieval_cache, corpus)
        question = _field(fields, "question") or b""
        if isinstance(question, bytes):
            question = question.decode(errors="replace")
        # The lexical leg and the dual-experience slots depend on the question's
        # terms, not only on its embedding: both go into the cache key.
        cache_query = "\0".join(
            (
                RETRIEVAL_MODE,
                " ".join(lexical_terms(question)),
                " ".join(tech_question_terms(question)),
            )
        )
        retrieval_key = retrieval_cache.key(
            corpus, version, embedding_model, embedding, query=cache_query
        )

        vector_started = time.monotonic()
        await stage("vector_search", "start")
        matches = await retrieval_cache.get(retrieval_key)
        retrieval_hit = matches is not None
        if matches is None:
            legs: dict = {}
            matches = [
                {"chunk_id": match["chunk_id"], "score": match["score"]}
                for match in await hybrid_search(
                    redis_client, embedding, question, corpus, embedding_model, legs=legs
                )
            ]
            # A vector-only fallback (BM25 query failed) must not fill the hybrid key.
            if not legs.get("fallback"):
                await retrieval_cache.put(retrieval_key, matches)
        await stage(
            "vector_search",
            "end",
            round((time.monotonic() - vector_started) * 1000),
            cache="hit" if retrieval_hit else "miss",
        )

        mysql_started = time.monotonic()
        await stage("mysql", "start")
        chunks = await chunk_cache.get(matches)
        chunk_hit = chunks is not None
        if chunks is None:
            chunks = _load_chunks(session_factory, matches)
            await chunk_cache.put(chunks)
        await stage(
            "mysql",
            "end",
            round((time.monotonic() - mysql_started) * 1000),
            cache="hit" if chunk_hit else "miss",
        )
        await emit("retrieval", {"chunks": chunks})
    except Exception:
        LOGGER.exception("Retrieval job %s failed", message_id)
        if channel:
            try:
                # Fixed text: the trace reaches the visitor's browser, and
                # exception text can carry SQL, hostnames or AWS error detail.
                # The full exception is in the worker log above.
                await emit("error", {"code": "internal", "message": RETRIEVAL_FAILED_MESSAGE})
            except Exception:
                LOGGER.exception("Could not publish failure for job %s", message_id)
    finally:
        await redis_client.xack(STREAM_NAME, GROUP_NAME, message_id)
    return True


async def run_worker() -> None:
    client = redis.from_url(os.environ["REDIS_URL"])
    consumer_name = os.environ.get("HOSTNAME") or f"worker-{uuid4().hex[:8]}"
    try:
        await ensure_consumer_group(client)
        session_factory = get_session_factory()
        while True:
            try:
                await process_one_message(client, session_factory, consumer_name=consumer_name)
            except Exception:
                LOGGER.exception("Worker loop failed; retrying")
                await asyncio.sleep(1)
    finally:
        await client.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()

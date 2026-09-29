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

from services.glassbox.db.models import Chunk, Document
from services.glassbox.db.session import get_session_factory
from services.glassbox.retrieval.search import VECTOR_DIMENSIONS, search_chunks
from services.glassbox.trace import elapsed_ms, next_seq

STREAM_NAME = "retrieval:jobs"
GROUP_NAME = "workers"
LOGGER = logging.getLogger(__name__)


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
        },
        maxlen=10000,
        approximate=True,
    )


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

    async def stage(node: str, status: str, duration_ms: int | None = None) -> None:
        payload = {"node": node, "status": status}
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
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

        vector_started = time.monotonic()
        await stage("vector_search", "start")
        matches = await search_chunks(redis_client, embedding, corpus, top_k=8)
        await stage("vector_search", "end", round((time.monotonic() - vector_started) * 1000))

        mysql_started = time.monotonic()
        await stage("mysql", "start")
        chunks = _load_chunks(session_factory, matches)
        await stage("mysql", "end", round((time.monotonic() - mysql_started) * 1000))
        await emit("retrieval", {"chunks": chunks})
    except Exception as exc:
        LOGGER.exception("Retrieval job %s failed", message_id)
        if channel:
            try:
                await emit("error", {"code": "internal", "message": str(exc)})
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

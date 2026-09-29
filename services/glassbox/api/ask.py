"""Stream the API and retrieval worker trace for a question."""

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Literal

import redis.asyncio as redis
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.glassbox.api.sse import frame
from services.glassbox.cache.embedding import (
    EmbeddingCache,
    RedisEmbeddingCache,
    embedding_cache_key,
    normalize_question,
)
from services.glassbox.db.models import Query
from services.glassbox.db.session import get_session_factory
from services.glassbox.providers.factory import get_embedding_provider, get_llm_provider
from services.glassbox.trace import elapsed_ms, next_seq
from services.glassbox.worker.main import enqueue_retrieval_job

router = APIRouter()
LOGGER = logging.getLogger(__name__)
RETRIEVAL_TIMEOUT_S = 30.0
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    corpus: Literal["about_me", "about_system"]


class WorkerStage(BaseModel):
    type: Literal["stage"]
    request_id: str
    seq: int
    t_ms: int
    node: Literal[
        "edge",
        "api",
        "answer_cache",
        "queue",
        "worker",
        "embed_cache",
        "embed",
        "vector_search",
        "mysql",
        "llm",
    ]
    status: Literal["start", "end"]
    duration_ms: int | None = None
    cache: Literal["hit", "miss"] | None = None
    meta: dict[str, str | int | float] | None = None


class WorkerChunk(BaseModel):
    n: int
    chunk_id: int
    text: str
    source_path: str
    title: str
    score: float
    start_line: int | None = None
    end_line: int | None = None
    url: str | None = None


class WorkerRetrieval(BaseModel):
    type: Literal["retrieval"]
    request_id: str
    seq: int
    t_ms: int
    chunks: list[WorkerChunk]


class WorkerError(BaseModel):
    type: Literal["error"]
    request_id: str
    seq: int
    t_ms: int
    code: Literal["rate_limited", "budget_exhausted", "internal"]
    message: str
    retry_after_s: int | None = None


def _ulid() -> str:
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_ULID_ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5))


def _prompt(question: str, chunks: list[WorkerChunk]) -> str:
    sources = "\n".join(f"[{chunk.n}] {chunk.source_path}: {chunk.text}" for chunk in chunks)
    return (
        "Answer the question using only the following numbered sources. "
        f"Cite sources by number.\n\n{sources}\n\nQuestion: {question}"
    )


def _save_query(
    *,
    request_id: str,
    request: AskRequest,
    chunks: list[WorkerChunk],
    timings: dict[str, int],
    total_ms: int,
    tokens_in: int,
    tokens_out: int,
) -> None:
    with get_session_factory()() as session:
        session.add(
            Query(
                request_id=request_id,
                corpus=request.corpus,
                question=request.question,
                cache_status="miss",
                mode="full",
                chunk_ids=[chunk.chunk_id for chunk in chunks],
                stage_timings_ms=timings,
                total_ms=total_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        )
        session.commit()


async def _stream(
    request: AskRequest, request_id: str, request_start_ts: int
) -> AsyncIterator[str]:
    client = redis.from_url(os.environ["REDIS_URL"])
    timings: dict[str, int] = {}

    async def stage(
        node: str,
        status: str,
        *,
        seq: int | None = None,
        duration_ms: int | None = None,
        cache: str | None = None,
        t_ms: int | None = None,
    ) -> str:
        payload = {
            "request_id": request_id,
            "seq": seq if seq is not None else await next_seq(client, request_id),
            "node": node,
            "status": status,
            "t_ms": elapsed_ms(request_start_ts) if t_ms is None else t_ms,
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
            timings[node] = duration_ms
        if cache is not None:
            payload["cache"] = cache
        return frame("stage", payload)

    try:
        yield await stage("api", "start", t_ms=0)
        provider = get_embedding_provider()
        cache: EmbeddingCache = RedisEmbeddingCache(client)
        cache_key = embedding_cache_key(request.question, provider.model_id)
        embedding = await cache.get(cache_key)
        yield await stage("embed_cache", "end", cache="hit" if embedding is not None else "miss")
        if embedding is None:
            embed_started = time.monotonic()
            yield await stage("embed", "start")
            embedding = (await provider.embed([normalize_question(request.question)]))[0]
            await cache.put(cache_key, embedding)
            yield await stage(
                "embed", "end", duration_ms=round((time.monotonic() - embed_started) * 1000)
            )
        yield await stage("answer_cache", "end", cache="miss")

        async with client.pubsub() as pubsub:
            await pubsub.subscribe(f"trace:{request_id}")
            yield await stage("queue", "start")
            # Reserve this sequence before publishing the job: a fast worker can
            # otherwise allocate a smaller sequence than the queue end event.
            queue_end_seq = await next_seq(client, request_id)
            await enqueue_retrieval_job(
                client,
                request_id=request_id,
                question=request.question,
                corpus=request.corpus,
                embedding=embedding,
                request_start_ts=request_start_ts,
                embedding_model=provider.model_id,
            )
            yield await stage("queue", "end", seq=queue_end_seq)

            deadline = time.monotonic() + RETRIEVAL_TIMEOUT_S
            chunks = None
            while chunks is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield frame(
                        "error",
                        {"code": "internal", "message": "Retrieval timed out after 30 seconds"},
                    )
                    return
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining),
                        timeout=remaining,
                    )
                except TimeoutError:
                    continue
                if message is None:
                    continue
                raw = json.loads(message["data"])
                kind = raw.get("type")
                if kind == "stage":
                    event = WorkerStage.model_validate(raw)
                    if event.request_id != request_id:
                        raise ValueError("worker trace request_id mismatch")
                    payload = event.model_dump(exclude={"type"}, exclude_none=True)
                    if event.duration_ms is not None:
                        timings[event.node] = event.duration_ms
                    yield frame("stage", payload)
                elif kind == "error":
                    event = WorkerError.model_validate(raw)
                    if event.request_id != request_id:
                        raise ValueError("worker trace request_id mismatch")
                    yield frame(
                        "error",
                        event.model_dump(
                            include={"code", "message", "retry_after_s"}, exclude_none=True
                        ),
                    )
                    return
                elif kind == "retrieval":
                    event = WorkerRetrieval.model_validate(raw)
                    if event.request_id != request_id:
                        raise ValueError("worker trace request_id mismatch")
                    chunks = event.chunks
                    yield frame(
                        "retrieval",
                        {
                            "chunks": [
                                chunk.model_dump(exclude={"text"}, exclude_none=True)
                                for chunk in chunks
                            ]
                        },
                    )
                else:
                    raise ValueError(f"unknown worker trace type: {kind}")

        prompt = _prompt(request.question, chunks)
        llm_started = time.monotonic()
        yield await stage("llm", "start")
        response_parts = []
        async for part in get_llm_provider().generate(prompt, max_tokens=400):
            response_parts.append(part)
            yield frame("token", {"text": part})
        yield await stage("llm", "end", duration_ms=round((time.monotonic() - llm_started) * 1000))
        total_ms = elapsed_ms(request_start_ts)
        tokens_in = len(prompt.split())
        tokens_out = len("".join(response_parts).split())
        await asyncio.to_thread(
            _save_query,
            request_id=request_id,
            request=request,
            chunks=chunks,
            timings=timings,
            total_ms=total_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        yield frame(
            "done",
            {
                "total_ms": total_ms,
                "mode": "full",
                "answer_cache": "miss",
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            },
        )
    except Exception:
        LOGGER.exception("Ask request %s failed", request_id)
        yield frame("error", {"code": "internal", "message": "The request could not be completed"})
    finally:
        await client.aclose()


@router.post("/api/ask")
async def ask(request: AskRequest) -> StreamingResponse:
    request_id = _ulid()
    request_start_ts = int(time.time() * 1000)
    return StreamingResponse(
        _stream(request, request_id, request_start_ts),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

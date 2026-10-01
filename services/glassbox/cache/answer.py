"""Semantic answer cache in Redis Search, validated against its source chunks.

An entry is keyed by corpus + model id (embedding model, LLM model and prompt
version) + question vector, not by the corpus-wide version that every ingest
bumps. Instead, each read checks that every chunk the answer was built from is
still indexed (``chunk:{id}`` exists). Ingest replaces all of a document's
chunks with fresh ids whenever its content (or embedding model) changes, and the
stale sweep deletes the chunks of removed documents, so a missing chunk key
means "a source document changed or was deleted": the entry is deleted and
treated as a miss. Edits to other documents leave the answer cached until its
24h TTL (DESIGN.md §7.3).
"""

import hashlib
import json
import logging
import struct
from typing import Protocol
from uuid import uuid4

from redis.exceptions import ResponseError

from services.glassbox.cache.cacheability import uncacheable_reason

LOGGER = logging.getLogger(__name__)

# v2: source-validated entries. The v1 index (``idx:answers`` over ``ans:``) held
# corpus-version-keyed entries without source checks; it is never read again and
# its keys expire via their TTL.
INDEX_NAME = "idx:answers:v2"
KEY_PREFIX = "ans2:"
ANSWER_TTL_S = 86400
MIN_SIMILARITY = 0.95
# Nearest entries checked per read: a stale nearest entry (deleted on sight) must
# not hide a valid one written after it for the same question.
_CANDIDATES = 3


class AnswerCache(Protocol):
    async def get(self, corpus: str, model_id: str, vector: list[float]) -> dict | None: ...

    async def put(self, corpus: str, model_id: str, vector: list[float], payload: dict) -> None: ...


def _model_tag(model_id: str) -> str:
    return hashlib.sha256(model_id.encode()).hexdigest()


def source_chunk_ids(payload: dict) -> set[int] | None:
    """The chunk ids an answer was built from, or None when the payload names none."""
    chunks = payload.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return None
    ids = set()
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id") if isinstance(chunk, dict) else None
        if not isinstance(chunk_id, int) or isinstance(chunk_id, bool):
            return None
        ids.add(chunk_id)
    return ids


class RedisAnswerCache:
    def __init__(self, client):
        self.client = client

    async def _ensure_index(self) -> None:
        try:
            await self.client.execute_command("FT.INFO", INDEX_NAME)
            return
        except ResponseError as exc:
            if "unknown index" not in str(exc).lower():
                raise
        try:
            await self.client.execute_command(
                "FT.CREATE",
                INDEX_NAME,
                "ON",
                "HASH",
                "PREFIX",
                "1",
                KEY_PREFIX,
                "SCHEMA",
                "corpus",
                "TAG",
                "model",
                "TAG",
                "vector",
                "VECTOR",
                "HNSW",
                "6",
                "TYPE",
                "FLOAT32",
                "DIM",
                "512",
                "DISTANCE_METRIC",
                "COSINE",
            )
        except ResponseError as exc:
            if "index already exists" not in str(exc).lower():
                raise

    async def sources_current(self, payload: dict) -> bool:
        """True when every source chunk of the answer is still in the chunk index."""
        ids = source_chunk_ids(payload)
        if not ids:
            return False
        present = await self.client.exists(*(f"chunk:{chunk_id}" for chunk_id in sorted(ids)))
        return present == len(ids)

    async def get(self, corpus: str, model_id: str, vector: list[float]) -> dict | None:
        await self._ensure_index()
        packed = struct.pack(f"<{len(vector)}f", *vector)
        query = (
            f"(@corpus:{{{corpus}}} @model:{{{_model_tag(model_id)}}})"
            f"=>[KNN {_CANDIDATES} @vector $vec AS distance]"
        )
        result = await self.client.execute_command(
            "FT.SEARCH",
            INDEX_NAME,
            query,
            "PARAMS",
            "2",
            "vec",
            packed,
            "SORTBY",
            "distance",
            "ASC",
            "RETURN",
            "1",
            "distance",
            "LIMIT",
            "0",
            str(_CANDIDATES),
            "DIALECT",
            "2",
        )
        if not result or result[0] == 0:
            return None
        for key, raw_fields in zip(result[1::2], result[2::2], strict=False):
            fields = dict(zip(raw_fields[::2], raw_fields[1::2], strict=True))
            distance = float(fields.get(b"distance", fields.get("distance")))
            if 1 - distance < MIN_SIMILARITY:
                break  # sorted by distance: the rest are further away
            payload = await self._load(key)
            if payload is None:
                continue
            if not await self.sources_current(payload):
                # A source document changed or was deleted since this answer was
                # written. Drop it so the next read doesn't re-check it.
                await self.client.delete(key)
                continue
            return payload
        return None

    async def _load(self, key) -> dict | None:
        raw = await self.client.hget(key, "payload")
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        # Defense in depth: a refusal or an empty answer is never served.
        if uncacheable_reason(payload.get("answer"), payload.get("chunks")):
            return None
        return payload

    async def put(self, corpus: str, model_id: str, vector: list[float], payload: dict) -> None:
        await self._ensure_index()
        # A source re-ingested while the answer was generating would make the entry
        # stale on arrival; skip it (reads re-check anyway, so this only saves space).
        if not await self.sources_current(payload):
            LOGGER.info("Answer cache write skipped: a source chunk is no longer indexed")
            return
        key = f"{KEY_PREFIX}{corpus}:{uuid4().hex}"
        packed = struct.pack(f"<{len(vector)}f", *vector)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "corpus": corpus,
                    "model": _model_tag(model_id),
                    "vector": packed,
                    "payload": json.dumps(payload),
                },
            )
            pipe.expire(key, ANSWER_TTL_S)
            await pipe.execute()

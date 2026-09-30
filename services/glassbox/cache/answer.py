"""Corpus-versioned semantic answer cache in Redis Search."""

import hashlib
import json
import struct
from typing import Protocol
from uuid import uuid4

from redis.exceptions import ResponseError

from services.glassbox.cache.cacheability import uncacheable_reason

INDEX_NAME = "idx:answers"
ANSWER_TTL_S = 86400
MIN_SIMILARITY = 0.95


class AnswerCache(Protocol):
    async def get(
        self, corpus: str, version: int, model_id: str, vector: list[float]
    ) -> dict | None: ...

    async def put(
        self, corpus: str, version: int, model_id: str, vector: list[float], payload: dict
    ) -> None: ...


def _model_tag(model_id: str) -> str:
    return hashlib.sha256(model_id.encode()).hexdigest()


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
                "ans:",
                "SCHEMA",
                "corpus",
                "TAG",
                "version",
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

    async def get(
        self, corpus: str, version: int, model_id: str, vector: list[float]
    ) -> dict | None:
        await self._ensure_index()
        packed = struct.pack(f"<{len(vector)}f", *vector)
        query = (
            f"(@corpus:{{{corpus}}} @version:{{{version}}} "
            f"@model:{{{_model_tag(model_id)}}})=>[KNN 1 @vector $vec AS distance]"
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
            "1",
            "DIALECT",
            "2",
        )
        if not result or result[0] == 0:
            return None
        fields = dict(zip(result[2][::2], result[2][1::2], strict=True))
        distance = float(fields.get(b"distance", fields.get("distance")))
        if 1 - distance < MIN_SIMILARITY:
            return None
        raw = await self.client.hget(result[1], "payload")
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        # Entries written before the cacheability gate may hold a refusal or an
        # empty answer; treat them as a miss so the question is answered afresh.
        if uncacheable_reason(payload.get("answer"), payload.get("chunks")):
            return None
        return payload

    async def put(
        self, corpus: str, version: int, model_id: str, vector: list[float], payload: dict
    ) -> None:
        await self._ensure_index()
        key = f"ans:{corpus}:v{version}:{uuid4().hex}"
        packed = struct.pack(f"<{len(vector)}f", *vector)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "corpus": corpus,
                    "version": str(version),
                    "model": _model_tag(model_id),
                    "vector": packed,
                    "payload": json.dumps(payload),
                },
            )
            pipe.expire(key, ANSWER_TTL_S)
            await pipe.execute()

"""Versioned Redis retrieval results and per-chunk text cache."""

import hashlib
import json
import struct
from typing import Protocol


class RetrievalCache(Protocol):
    async def version(self, corpus: str) -> int: ...

    def key(self, corpus: str, version: int, model_id: str, vector: list[float]) -> str: ...

    async def get(self, key: str) -> list[dict] | None: ...

    async def put(self, key: str, matches: list[dict]) -> None: ...


class RedisRetrievalCache:
    def __init__(self, client):
        self.client = client

    async def version(self, corpus: str) -> int:
        raw = await self.client.get(f"corpus:ver:{corpus}")
        return int(raw) if raw is not None else 0

    def key(self, corpus: str, version: int, model_id: str, vector: list[float]) -> str:
        packed = struct.pack(f"<{len(vector)}f", *vector)
        fingerprint = hashlib.sha256(model_id.encode() + b"\0" + packed).hexdigest()
        return f"ret:{corpus}:v{version}:{fingerprint}"

    async def get(self, key: str) -> list[dict] | None:
        raw = await self.client.get(key)
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            if isinstance(value, list) and all(
                isinstance(row, dict)
                and isinstance(row.get("chunk_id"), int)
                and isinstance(row.get("score"), int | float)
                for row in value
            ):
                return value
        except (TypeError, ValueError):
            pass
        return None

    async def put(self, key: str, matches: list[dict]) -> None:
        await self.client.set(key, json.dumps(matches), ex=3600)


class ChunkCache(Protocol):
    async def get(self, matches: list[dict]) -> list[dict] | None: ...

    async def put(self, chunks: list[dict]) -> None: ...


class RedisChunkCache:
    def __init__(self, client):
        self.client = client

    async def get(self, matches: list[dict]) -> list[dict] | None:
        if not matches:
            return []
        raw = await self.client.mget([f"chunktxt:{m['chunk_id']}" for m in matches])
        if any(value is None for value in raw):
            return None
        try:
            stored = [json.loads(value) for value in raw]
            if any(not isinstance(item, dict) or "text" not in item for item in stored):
                return None
        except (TypeError, ValueError):
            return None
        return [
            {"n": rank, "chunk_id": match["chunk_id"], "score": match["score"], **item}
            for rank, (match, item) in enumerate(zip(matches, stored, strict=True), start=1)
        ]

    async def put(self, chunks: list[dict]) -> None:
        for chunk in chunks:
            metadata = {
                key: value for key, value in chunk.items() if key not in {"n", "score", "chunk_id"}
            }
            await self.client.set(f"chunktxt:{chunk['chunk_id']}", json.dumps(metadata), ex=86400)

"""KNN search over Phase 1a's Redis Search chunk index."""

import struct

from services.glassbox.ingest.redis_index import INDEX_NAME

VECTOR_DIMENSIONS = 512


async def search_chunks(
    redis_client, embedding: list[float], corpus: str, top_k: int = 8
) -> list[dict]:
    """Return chunk IDs and cosine similarities in nearest-first order."""
    if len(embedding) != VECTOR_DIMENSIONS:
        raise ValueError(f"embedding must have {VECTOR_DIMENSIONS} float32 values")
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if corpus not in ("about_me", "about_system"):
        raise ValueError("unknown corpus")

    vector = struct.pack(f"{VECTOR_DIMENSIONS}f", *embedding)
    query = f"(@corpus:{{{corpus}}})=>[KNN {top_k} @vector $vec AS distance]"
    response = await redis_client.execute_command(
        "FT.SEARCH",
        INDEX_NAME,
        query,
        "PARAMS",
        "2",
        "vec",
        vector,
        "SORTBY",
        "distance",
        "ASC",
        "RETURN",
        "1",
        "distance",
        "LIMIT",
        "0",
        str(top_k),
        "DIALECT",
        "2",
    )
    matches = []
    for key, fields in zip(response[1::2], response[2::2], strict=True):
        key = key.decode() if isinstance(key, bytes) else key
        values = dict(zip(fields[::2], fields[1::2], strict=True))
        raw_distance = values.get(b"distance", values.get("distance"))
        matches.append(
            {"chunk_id": int(key.removeprefix("chunk:")), "score": 1 - float(raw_distance)}
        )
    return matches

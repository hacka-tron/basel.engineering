"""KNN search over Phase 1a's Redis Search chunk index."""

import struct
from collections.abc import Iterable

from services.glassbox.cache.answer import _model_tag
from services.glassbox.corpora import CORPORA
from services.glassbox.ingest.redis_index import INDEX_NAME, KINDS

VECTOR_DIMENSIONS = 512


def knn_query(corpus: str, model_id: str, top_k: int, kinds: Iterable[str] | None = None) -> str:
    """The FT.SEARCH KNN query: corpus and model tags, plus an optional ``kind`` filter."""
    kind_filter = ""
    if kinds is not None:
        kinds = sorted(set(kinds))
        if not kinds or any(kind not in KINDS for kind in kinds):
            raise ValueError(f"kinds must be a non-empty subset of {KINDS}")
        kind_filter = f" @kind:{{{'|'.join(kinds)}}}"
    return (
        f"(@corpus:{{{corpus}}} @model:{{{_model_tag(model_id)}}}{kind_filter})"
        f"=>[KNN {top_k} @vector $vec AS distance]"
    )


async def search_chunks(
    redis_client,
    embedding: list[float],
    corpus: str,
    model_id: str,
    top_k: int = 8,
    kinds: Iterable[str] | None = None,
) -> list[dict]:
    """Return chunk IDs and cosine similarities in nearest-first order.

    ``kinds`` (optional) keeps only chunks whose ``kind`` tag is one of them
    (``redis_index.KINDS``); ``None`` means no kind filter. A key written before
    the ``kind`` field existed matches no kind filter until reconcile rewrites it.
    """
    if len(embedding) != VECTOR_DIMENSIONS:
        raise ValueError(f"embedding must have {VECTOR_DIMENSIONS} float32 values")
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if corpus not in CORPORA:
        raise ValueError("unknown corpus")

    vector = struct.pack(f"{VECTOR_DIMENSIONS}f", *embedding)
    # Empty results after a provider switch flow to the API's no-sources answer.
    query = knn_query(corpus, model_id, top_k, kinds)
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

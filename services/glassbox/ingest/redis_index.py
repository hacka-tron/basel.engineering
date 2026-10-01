"""Redis Search index for 512-dimensional chunk embeddings."""

from redis.exceptions import ResponseError

from services.glassbox.cache.answer import _model_tag, chunk_content_sha

INDEX_NAME = "idx:chunks"
# Set a field on an existing hash only: EXISTS then HSET could recreate a key deleted
# in between, holding nothing but the model field.
_SET_FIELD_IF_EXISTS = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
end
return -1
"""
CONTENT_SHA_READY_KEY = "idx:chunks:content-sha-ready"


async def ensure_index(client) -> bool:
    """Ensure the model field exists; return whether old hashes need backfilling."""
    try:
        info = await client.execute_command("FT.INFO", INDEX_NAME)
        attributes = info[info.index(b"attributes") + 1]
        if any(b"model" in attribute for attribute in attributes):
            return False
        await client.execute_command("FT.ALTER", INDEX_NAME, "SCHEMA", "ADD", "model", "TAG")
        return True
    except ResponseError as exc:
        if "unknown index" not in str(exc).lower():
            raise
    try:
        await client.execute_command(
            "FT.CREATE",
            INDEX_NAME,
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "chunk:",
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
    return True


async def backfill_model_tags(client, rows: list[tuple[int, str]]) -> None:
    """Tag existing hashes from MySQL, without creating incomplete hashes for missing keys."""
    for chunk_id, model_id in rows:
        await client.eval(
            _SET_FIELD_IF_EXISTS, 1, f"chunk:{chunk_id}", "model", _model_tag(model_id)
        )


async def backfill_content_shas(client, rows) -> None:
    """Add ``content_sha`` to existing chunk hashes from MySQL ``(id, text)`` rows.

    Hashes written before the field existed would otherwise never get it (ingest
    skips unchanged documents), and the answer cache treats a missing
    ``content_sha`` as a changed source. Never creates a key that is gone.
    """
    for chunk_id, text in rows:
        await client.eval(
            _SET_FIELD_IF_EXISTS, 1, f"chunk:{chunk_id}", "content_sha", chunk_content_sha(text)
        )


async def replace_document_vectors(
    client,
    old_ids: list[int],
    chunks: list[tuple[int, str, bytes, str, int, str]],
    model_id: str,
) -> None:
    """Remove former chunk keys and write new hashes in one Redis pipeline.

    Each chunk is ``(id, corpus, packed vector, source_path, document_id, text)``;
    the hash stores ``content_sha`` of the text, not the text itself.
    """
    async with client.pipeline(transaction=True) as pipeline:
        for chunk_id in old_ids:
            pipeline.delete(f"chunk:{chunk_id}")
        for chunk_id, corpus, vector, source_path, document_id, text in chunks:
            pipeline.hset(
                f"chunk:{chunk_id}",
                mapping={
                    "corpus": corpus,
                    "model": _model_tag(model_id),
                    "vector": vector,
                    "source_path": source_path,
                    "document_id": document_id,
                    "content_sha": chunk_content_sha(text),
                },
            )
        await pipeline.execute()

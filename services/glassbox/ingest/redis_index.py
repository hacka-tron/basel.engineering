"""Redis Search index for 512-dimensional chunk embeddings."""

from redis.exceptions import ResponseError

INDEX_NAME = "idx:chunks"


async def ensure_index(client) -> None:
    try:
        await client.execute_command("FT.INFO", INDEX_NAME)
        return
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


async def replace_document_vectors(
    client, old_ids: list[int], chunks: list[tuple[int, str, bytes, str, int]]
) -> None:
    """Remove former chunk keys and write new hashes in one Redis pipeline."""
    async with client.pipeline(transaction=True) as pipeline:
        for chunk_id in old_ids:
            pipeline.delete(f"chunk:{chunk_id}")
        for chunk_id, corpus, vector, source_path, document_id in chunks:
            pipeline.hset(
                f"chunk:{chunk_id}",
                mapping={
                    "corpus": corpus,
                    "vector": vector,
                    "source_path": source_path,
                    "document_id": document_id,
                },
            )
        await pipeline.execute()

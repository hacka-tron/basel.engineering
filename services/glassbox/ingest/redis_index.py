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


# Every field ``idx:chunks`` must have, in FT.CREATE order: (name, schema args).
# ``ensure_index`` adds whichever of these an existing index lacks with FT.ALTER,
# so a new field (``kind`` here, phase 8's ``text`` later) is one line in this list.
VECTOR_SCHEMA = (
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
EXPECTED_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("corpus", ("TAG",)),
    ("model", ("TAG",)),
    ("kind", ("TAG",)),
    ("vector", VECTOR_SCHEMA),
)
# The ``kind`` tag values: what sort of source a chunk came from.
KINDS = ("doc", "code", "infra", "manifest", "test")


def chunk_kind(corpus: str, source_path: str) -> str:
    """Classify a source for the ``kind`` tag (doc, code, infra, manifest or test).

    About Basel and portfolio documents are always ``doc``. In About This System,
    Markdown is ``doc``; test files (a ``tests`` folder, ``test_*.py``,
    ``*_test.py``, ``*.test.ts(x)``) are ``test`` even though the scanner skips
    ``services/tests/`` today; Terraform is ``infra``; YAML (Kubernetes manifests,
    workflows) is ``manifest``; Python and TypeScript are ``code``.
    """
    if corpus != "about_system":
        return "doc"
    parts = source_path.split("/")
    name = parts[-1]
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix == "md":
        return "doc"
    if (
        "tests" in parts[:-1]
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
    ):
        return "test"
    if suffix == "tf":
        return "infra"
    if suffix in ("yml", "yaml"):
        return "manifest"
    if suffix in ("py", "ts", "tsx"):
        return "code"
    return "doc"


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def index_field_names(info) -> set[str]:
    """The attribute names in an ``FT.INFO`` reply (bytes or str, RESP2 list form)."""
    info = [_text(item) if isinstance(item, bytes | str) else item for item in info]
    names = set()
    for attribute in info[info.index("attributes") + 1]:
        attribute = [_text(item) if isinstance(item, bytes | str) else item for item in attribute]
        key = "attribute" if "attribute" in attribute else "identifier"
        names.add(attribute[attribute.index(key) + 1])
    return names


async def ensure_index(client) -> frozenset[str]:
    """Create ``idx:chunks``, or add every expected field an existing one lacks.

    Returns the names of the fields this call added (all of them when it created
    the index). Hashes written before a field existed don't carry it: the caller
    backfills ``model`` from MySQL, and the reconcile step rewrites any key whose
    ``kind`` differs from its row (``reconcile._COMPARED``). FT.ALTER only extends
    the schema; a field can't be removed or retyped this way.
    """
    try:
        info = await client.execute_command("FT.INFO", INDEX_NAME)
    except ResponseError as exc:
        if "unknown index" not in str(exc).lower():
            raise
    else:
        present = index_field_names(info)
        added = []
        for name, schema in EXPECTED_FIELDS:
            if name not in present:
                await client.execute_command("FT.ALTER", INDEX_NAME, "SCHEMA", "ADD", name, *schema)
                added.append(name)
        return frozenset(added)
    schema = [part for name, args in EXPECTED_FIELDS for part in (name, *args)]
    try:
        await client.execute_command(
            "FT.CREATE", INDEX_NAME, "ON", "HASH", "PREFIX", "1", "chunk:", "SCHEMA", *schema
        )
    except ResponseError as exc:
        if "index already exists" not in str(exc).lower():
            raise
    return frozenset(name for name, _ in EXPECTED_FIELDS)


async def backfill_model_tags(client, rows: list[tuple[int, str]]) -> None:
    """Tag existing hashes from MySQL, without creating incomplete hashes for missing keys."""
    for chunk_id, model_id in rows:
        await client.eval(
            _SET_FIELD_IF_EXISTS, 1, f"chunk:{chunk_id}", "model", _model_tag(model_id)
        )


def chunk_fields(
    corpus: str, model_id: str, vector: bytes, source_path: str, document_id: int, text: str
) -> dict:
    """The one definition of a ``chunk:{id}`` hash; every writer goes through it.

    ``content_sha`` lets the reconcile step (and the answer cache) check that a key
    still describes the MySQL row with the same id, without reading the vector.
    """
    return {
        "corpus": corpus,
        "model": _model_tag(model_id),
        "kind": chunk_kind(corpus, source_path),
        "vector": vector,
        "source_path": source_path,
        "document_id": document_id,
        "content_sha": chunk_content_sha(text),
    }


async def replace_document_vectors(
    client, old_ids: list[int], chunks: list[tuple[int, str, bytes, str, int, str]], model_id: str
) -> None:
    """Remove former chunk keys and write new hashes in one Redis pipeline.

    Each chunk is ``(id, corpus, packed vector, source_path, document_id, text)``.
    The ``chunktxt:{id}`` text cache of every old and new id is dropped too: ids
    restart after a MySQL wipe or restore, and a new ``chunk:5`` must not be
    served the old ``chunk:5``'s cached text for the rest of that cache's day.
    """
    async with client.pipeline(transaction=True) as pipeline:
        for chunk_id in old_ids:
            pipeline.delete(f"chunk:{chunk_id}", f"chunktxt:{chunk_id}")
        for chunk_id, corpus, vector, source_path, document_id, text in chunks:
            pipeline.delete(f"chunktxt:{chunk_id}")
            pipeline.hset(
                f"chunk:{chunk_id}",
                mapping=chunk_fields(corpus, model_id, vector, source_path, document_id, text),
            )
        await pipeline.execute()

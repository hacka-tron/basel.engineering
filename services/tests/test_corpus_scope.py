"""RAG plan phase 6 part 2: corpus scope, the ``kind`` tag and ``ensure_index`` migration."""

import struct
from uuid import uuid4

import pytest
import redis.asyncio as redis

from services.glassbox.cache.answer import _model_tag
from services.glassbox.ingest import redis_index
from services.glassbox.ingest.reconcile import ScopeRow, plan_reconcile
from services.glassbox.ingest.redis_index import (
    EXPECTED_FIELDS,
    KINDS,
    chunk_content_sha,
    chunk_fields,
    chunk_kind,
    ensure_index,
    index_field_names,
)
from services.glassbox.ingest.run import seen_source_paths
from services.glassbox.ingest.scanner import (
    EXCLUDED_SYSTEM_PREFIXES,
    excluded_system_path,
    scan_sources,
)
from services.glassbox.ingest.sweep import ScopedDocument, plan_sweep
from services.glassbox.retrieval.search import knn_query, search_chunks
from services.tests.stack_ports import redis_url_for

VECTOR = [1.0] + [0.0] * 511


def _write(root, relative, text="x = 1\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- scanner exclusions -------------------------------------------------------


def test_scanner_skips_tests_and_plans_but_keeps_neighbours(tmp_path):
    for relative in (
        "services/tests/test_api.py",
        "services/tests/fixtures/sample.md",
        "services/tests/secret.tfvars",  # excluded before the denylist even sees it
        "docs/superpowers/plans/2026-10-01-plan.md",
        "docs/superpowers/specs/spec.md",
        "docs/DESIGN.md",
        "services/glassbox/api/ask.py",
        "services/testsuite.py",  # only the folder is excluded, not the prefix text
    ):
        _write(tmp_path, relative)
    paths = {source.source_path for source in scan_sources(tmp_path)}
    assert paths == {
        "docs/superpowers/specs/spec.md",
        "docs/DESIGN.md",
        "services/glassbox/api/ask.py",
        "services/testsuite.py",
    }


def test_repo_scan_has_no_excluded_or_frontend_paths():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    paths = [source.source_path for source in scan_sources(root)]
    assert paths, "the repo scan found nothing"
    assert not [path for path in paths if path.startswith(EXCLUDED_SYSTEM_PREFIXES)]
    assert not [path for path in paths if path.startswith("frontend/")]
    assert "docs/DESIGN.md" in paths


def test_eval_noise_prefixes_match_the_scanner_exclusions():
    """``noise@8`` counts exactly what the scanner leaves out, so it reads 0 after re-ingest."""
    from eval.run_eval import NOISE_PREFIXES

    assert set(NOISE_PREFIXES) == set(EXCLUDED_SYSTEM_PREFIXES)


def test_excluded_file_is_swept_like_a_deleted_one(tmp_path):
    """A file indexed before the exclusion is unseen now, so the sweep lists it as stale."""
    _write(tmp_path, "services/tests/test_old.py")
    _write(tmp_path, "services/glassbox/api/ask.py")
    _write(tmp_path, "docs/DESIGN.md", "# Design\n")
    seen = seen_source_paths(tmp_path)["about_system"]
    known = [
        ScopedDocument(1, "services/tests/test_old.py", (10,)),
        ScopedDocument(2, "services/glassbox/api/ask.py", (20,)),
        ScopedDocument(3, "docs/DESIGN.md", (30,)),
    ]
    plan = plan_sweep("about_system", "m", known, seen)
    assert [doc.source_path for doc in plan.stale] == ["services/tests/test_old.py"]
    assert plan.refused is None
    assert excluded_system_path("services/tests/test_old.py")


# --- kind tag -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("corpus", "path", "kind"),
    [
        ("about_me", "private/bio.md", "doc"),
        ("portfolio", "corpus/portfolio/x.md", "doc"),
        ("about_system", "docs/DESIGN.md", "doc"),
        ("about_system", "services/tests/README.md", "doc"),
        ("about_system", "services/glassbox/api/ask.py", "code"),
        ("about_system", "services/glassbox/web/app.tsx", "code"),
        ("about_system", "infra/main.tf", "infra"),
        ("about_system", "k8s/base/api.yaml", "manifest"),
        ("about_system", "k8s/overlays/prod/kustomization.yml", "manifest"),
        ("about_system", "services/tests/test_api.py", "test"),
        ("about_system", "services/glassbox/test_helpers.py", "test"),
        ("about_system", "services/glassbox/thing_test.py", "test"),
        ("about_system", "services/glassbox/web/app.test.ts", "test"),
    ],
)
def test_chunk_kind(corpus, path, kind):
    assert chunk_kind(corpus, path) == kind
    assert kind in KINDS


def test_chunk_fields_carry_kind():
    fields = chunk_fields("about_system", "m", b"v", "infra/main.tf", 1, "text")
    assert fields["kind"] == "infra"


def test_reconcile_rewrites_a_key_written_before_kind_existed():
    row = ScopeRow(7, "about_system", 1, "infra/main.tf", chunk_content_sha("t"))
    old = ("about_system", _model_tag("m"), "1", "infra/main.tf", row.content_sha, None)
    assert plan_reconcile("m", [row], {7: old})["about_system"].rewritten == [7]
    current = (*old[:-1], "infra")
    assert plan_reconcile("m", [row], {7: current})["about_system"].rewritten == []


# --- ensure_index -------------------------------------------------------------


def _info(*names, as_bytes=True):
    encode = (lambda value: value.encode()) if as_bytes else (lambda value: value)
    attributes = [
        [encode("identifier"), encode(name), encode("attribute"), encode(name), encode("type")]
        for name in names
    ]
    return [encode("index_name"), encode("idx:chunks"), encode("attributes"), attributes]


class _FakeIndexClient:
    def __init__(self, fields=None):
        self.fields = fields
        self.commands = []

    async def execute_command(self, *args):
        self.commands.append(args)
        if args[0] == "FT.INFO":
            if self.fields is None:
                from redis.exceptions import ResponseError

                raise ResponseError("Unknown index name")
            return _info(*self.fields)
        if args[0] == "FT.ALTER":
            self.fields.append(args[4])
        return b"OK"


@pytest.mark.parametrize("as_bytes", [True, False])
def test_index_field_names_reads_bytes_and_str(as_bytes):
    assert index_field_names(_info("corpus", "model", as_bytes=as_bytes)) == {"corpus", "model"}


@pytest.mark.asyncio
async def test_ensure_index_adds_only_kind_to_an_index_with_model():
    client = _FakeIndexClient(["corpus", "model", "vector"])
    assert await ensure_index(client) == frozenset({"kind"})
    assert ("FT.ALTER", "idx:chunks", "SCHEMA", "ADD", "kind", "TAG") in client.commands
    client.commands.clear()
    assert await ensure_index(client) == frozenset()
    assert [command[0] for command in client.commands] == ["FT.INFO"]


@pytest.mark.asyncio
async def test_ensure_index_adds_every_missing_field():
    """An index from before the model tag gets both ``model`` and ``kind``."""
    client = _FakeIndexClient(["corpus", "vector"])
    assert await ensure_index(client) == frozenset({"model", "kind"})
    altered = [command[4] for command in client.commands if command[0] == "FT.ALTER"]
    assert altered == ["model", "kind"]


@pytest.mark.asyncio
async def test_ensure_index_adds_a_future_field(monkeypatch):
    """Phase 8's ``text`` field is one more EXPECTED_FIELDS entry, added the same way."""
    monkeypatch.setattr(redis_index, "EXPECTED_FIELDS", (*EXPECTED_FIELDS, ("text", ("TEXT",))))
    client = _FakeIndexClient(["corpus", "model", "kind", "vector"])
    assert await ensure_index(client) == frozenset({"text"})
    assert ("FT.ALTER", "idx:chunks", "SCHEMA", "ADD", "text", "TEXT") in client.commands


@pytest.mark.asyncio
async def test_ensure_index_creates_with_full_schema():
    client = _FakeIndexClient(None)
    assert await ensure_index(client) == frozenset(name for name, _ in EXPECTED_FIELDS)
    create = next(command for command in client.commands if command[0] == "FT.CREATE")
    schema = create[create.index("SCHEMA") + 1 :]
    assert [schema[i] for i in (0, 2, 4)] == ["corpus", "model", "kind"]
    assert schema[6] == "vector" and "VECTOR" in schema


def test_knn_query_kind_filter():
    base = knn_query("about_system", "m", 8)
    assert "@kind" not in base
    filtered = knn_query("about_system", "m", 8, kinds=["infra", "doc", "doc"])
    assert "@kind:{doc|infra})" in filtered
    for bad in ([], ["frontend"]):
        with pytest.raises(ValueError, match="kinds"):
            knn_query("about_system", "m", 8, kinds=bad)


@pytest.mark.asyncio
async def test_search_chunks_passes_the_kind_filter():
    class Client:
        async def execute_command(self, *args):
            self.query = args[2]
            return [0]

    client = Client()
    assert await search_chunks(client, VECTOR, "about_system", "m", kinds=["code"]) == []
    assert "@kind:{code}" in client.query


@pytest.mark.asyncio
async def test_ensure_index_ft_alter_on_real_redis_adds_kind_and_filter_works(monkeypatch):
    """Against Redis Stack: an old (corpus, model, vector) index gains ``kind``; KNN filters on it.

    Uses its own index name and key prefix, so the shared ``idx:chunks`` is untouched.
    """
    client = redis.from_url(redis_url_for())
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis Stack unavailable: {exc}")
    token = uuid4().hex[:12]
    name, prefix = f"idx:p6test:{token}", f"p6test:{token}:"
    monkeypatch.setattr(redis_index, "INDEX_NAME", name)
    packed = struct.pack("512f", *VECTOR)
    model = f"m-{token}"

    async def found(kinds):
        response = await client.execute_command(
            "FT.SEARCH", name, knn_query("about_system", model, 8, kinds),
            "PARAMS", "2", "vec", packed, "RETURN", "0", "DIALECT", "2",
        )  # fmt: skip
        return {key.decode().removeprefix(prefix) for key in response[1:]}

    try:
        await client.execute_command(
            "FT.CREATE", name, "ON", "HASH", "PREFIX", "1", prefix, "SCHEMA",
            "corpus", "TAG", "model", "TAG", "vector", *redis_index.VECTOR_SCHEMA,
        )  # fmt: skip
        assert await ensure_index(client) == frozenset({"kind"})
        assert "kind" in index_field_names(await client.execute_command("FT.INFO", name))
        assert await ensure_index(client) == frozenset()
        for chunk_id, path in ((1, "docs/a.md"), (2, "infra/main.tf")):
            fields = chunk_fields("about_system", model, packed, path, chunk_id, path)
            await client.hset(f"{prefix}{chunk_id}", mapping=fields)
        await client.hset(  # written before ``kind``: invisible to a kind filter only
            f"{prefix}3",
            mapping={"corpus": "about_system", "model": _model_tag(model), "vector": packed},
        )
        assert await found(None) == {"1", "2", "3"}
        assert await found(["infra"]) == {"2"}
        assert await found(["doc", "infra"]) == {"1", "2"}
    finally:
        await client.execute_command("FT.DROPINDEX", name)
        await client.delete(*(f"{prefix}{i}" for i in (1, 2, 3)))
        await client.aclose()

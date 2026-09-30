"""Scanner checks and real MySQL/Redis ingestion coverage."""

import hashlib
import json
from pathlib import Path

import pytest
import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.glassbox.db.models import Base, Document, IngestionRun
from services.glassbox.db.models import Chunk as DbChunk
from services.glassbox.db.session import create_db_engine
from services.glassbox.ingest import run as ingest_run
from services.glassbox.ingest.run import chunker_for_path, ingest
from services.glassbox.ingest.scanner import (
    scan_file,
    scan_sources,
    secret_reason,
    strip_front_matter,
)
from services.glassbox.providers.fake import FakeEmbeddingProvider


def test_front_matter_and_dispatch(tmp_path):
    body = "# Heading\n\nContent.\n"
    assert strip_front_matter("---\ntype: bio\n---\n" + body) == body
    assert strip_front_matter(body) == body
    assert chunker_for_path(Path("item.md"))(body, "item.md")[0].text == body
    for extension in (".tf", ".yml", ".yaml", ".py", ".ts", ".tsx"):
        assert chunker_for_path(Path("item" + extension)) is not None
    assert chunker_for_path(Path("item.txt")) is None


def test_scanner_quarantines_secret_and_denylisted_paths(tmp_path):
    root = tmp_path
    about_me = root / "corpus" / "about-me"
    about_me.mkdir(parents=True)
    (about_me / "safe.md").write_text("# Safe\n")
    (about_me / "key.md").write_text("# Unsafe\naws_key = AKIA1234567890ABCDEF\n")
    (about_me / ".env.md").write_text("# Hidden\n")
    candidates = list(scan_sources(root))
    results = {source.source_path: scan_file(source) for source in candidates}
    assert results["corpus/about-me/safe.md"].error is None
    assert (
        results["corpus/about-me/safe.md"].content_hash == hashlib.sha256(b"# Safe\n").hexdigest()
    )
    assert results["corpus/about-me/key.md"].error is not None
    assert results["corpus/about-me/.env.md"].error is not None


def test_system_scanner_ignores_unsupported_files_and_missing_directories(tmp_path):
    services = tmp_path / "services"
    services.mkdir()
    (services / "api.py").write_text("def run():\n    pass\n")
    (services / "notes.txt").write_text("Skip me")
    (services / "secret.tfvars").write_text("credential = hidden")
    sources = {source.source_path: source for source in scan_sources(tmp_path)}
    assert set(sources) == {"services/api.py", "services/secret.tfvars"}
    assert scan_file(sources["services/secret.tfvars"]).error == "denylisted path"


@pytest.mark.parametrize(
    "relative_path",
    ["services/secrets.yml", "services/Secrets/main.py", "services/.ENV", "services/config.TFVARS"],
)
def test_scanner_denylist_is_case_insensitive_and_catches_secrets_files(tmp_path, relative_path):
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("safe content")
    sources = {source.source_path: source for source in scan_sources(tmp_path)}
    assert relative_path in sources
    assert scan_file(sources[relative_path]).error == "denylisted path"


def test_secret_heuristic_catches_private_key_and_high_entropy_assignment():
    assert secret_reason("-----BEGIN RSA PRIVATE KEY-----") == "private key header at line 1"
    assert secret_reason("token = 'aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5z'")


@pytest.mark.parametrize(
    "assignment",
    [
        'api_key = "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5z"',
        '"api_key": "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5z"',
    ],
)
def test_secret_heuristic_catches_quoted_and_unquoted_keys(assignment):
    assert secret_reason(assignment) == "possible high-entropy assigned value at line 1"


def test_secret_heuristic_catches_temporary_aws_key():
    assert secret_reason("key = ASIA1234567890ABCDEF") == "possible AWS access key at line 1"


def test_architecture_deep_dive_is_ingested_into_about_system():
    repo_root = Path(__file__).resolve().parents[2]
    deep_dive = "docs/architecture/deep-dive.md"
    sources = {source.source_path: source for source in scan_sources(repo_root)}

    assert sources[deep_dive].corpus == "about_system"
    scanned = scan_file(sources[deep_dive])
    assert scanned.error is None
    assert scanned.content is not None

    # One retrievable chunk per H2 section: every chunk starts at a heading, no
    # section is split or merged, and unbuilt work stays in its own last chunk.
    chunks = chunker_for_path(Path(deep_dive))(scanned.content, deep_dive)
    sections = [line for line in scanned.content.splitlines() if line.startswith("## ")]
    assert len(chunks) == len(sections) == 25
    assert chunks[0].text.startswith("# Glassbox architecture deep dive")
    for chunk, heading in zip(chunks[1:], sections[1:], strict=True):
        assert chunk.text.startswith(heading)
    assert chunks[-1].text.startswith("## Planned / not built yet")
    assert all(chunk.token_count <= 500 for chunk in chunks)


def dockerfile_copy_sources(dockerfile: str) -> set[str]:
    """Top-level repo paths that COPY/ADD instructions take from the build context.

    Joins backslash continuations, skips flags such as --chown and --link, reads the
    JSON-array form, and ignores copies from another build stage (--from=...).
    """
    logical, pending = [], ""
    for raw in dockerfile.splitlines():
        line = raw.rstrip()
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        logical.append(pending + line)
        pending = ""
    if pending:
        logical.append(pending)

    sources: set[str] = set()
    for line in logical:
        instruction, _, rest = line.strip().partition(" ")
        if instruction.upper() not in {"COPY", "ADD"}:
            continue
        rest = rest.strip()
        flags = []
        while rest.startswith("--"):
            flag, _, rest = rest.partition(" ")
            flags.append(flag)
            rest = rest.strip()
        if any(flag.startswith("--from") for flag in flags):
            continue
        parts = json.loads(rest) if rest.startswith("[") else rest.split()
        for source in parts[:-1]:
            top = source.removeprefix("./").strip("/").split("/", 1)[0]
            if top and top != ".":
                sources.add(top)
    return sources


def test_dockerfile_copy_sources_parser_handles_continuations_flags_and_json():
    dockerfile = """FROM node:22 AS build
# COPY commented/ out/
COPY frontend/package.json frontend/package-lock.json ./
COPY docs/ \\
    newdir/ /app/
COPY --chown=app:app --link corpus/ /app/corpus/
COPY ["infra/", "k8s/base/x.yaml", "/app/"]
ADD alembic.ini ./
COPY --from=build /app/frontend/dist frontend/dist
COPY --from build /tmp/x /tmp/x
"""
    assert dockerfile_copy_sources(dockerfile) == {
        "frontend",
        "docs",
        "newdir",
        "corpus",
        "infra",
        "k8s",
        "alembic.ini",
    }


def test_release_workflow_rebuilds_on_every_path_copied_into_the_image():
    """A docs- or corpus-only merge must build a release, or ingest never sees it."""
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github/workflows/release.yml").read_text()
    triggers = {
        line.strip().removeprefix("- ").removesuffix("/**")
        for line in workflow.split("paths:", 1)[1].split("concurrency:", 1)[0].splitlines()
        if line.strip().startswith("- ")
    }
    copied = dockerfile_copy_sources((repo_root / "Dockerfile").read_text())
    assert {"services", "docs", "corpus"} <= copied
    assert copied <= triggers, f"release.yml paths miss: {sorted(copied - triggers)}"


@pytest.fixture
def integration_stack(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_USER", "glassbox")
    monkeypatch.setenv("MYSQL_PASSWORD", "glassbox")
    monkeypatch.setenv("MYSQL_DATABASE", "glassbox")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    engine = create_db_engine()
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")
    Base.metadata.create_all(engine)
    yield engine, client
    engine.dispose()


@pytest.mark.asyncio
async def test_ingest_happy_path_idempotency_and_quarantine(tmp_path, integration_stack):
    engine, client = integration_stack
    try:
        await client.ping()
    except Exception as exc:
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")
    about_me = tmp_path / "corpus" / "about-me"
    about_me.mkdir(parents=True)
    fixture_name = f"{tmp_path.name}.md"
    source_path = f"corpus/about-me/{fixture_name}"
    (about_me / fixture_name).write_text("---\ntype: bio\n---\n# Fixture\n\nBody.\n")
    (about_me / "unsafe.md").write_text("# Unsafe\nkey = AKIA1234567890ABCDEF\n")
    with engine.connect() as connection:
        existing_run_ids = set(connection.scalars(select(IngestionRun.id)))
    starting_version = int(await client.get("corpus:ver:about_me") or 0)
    redis_keys = []
    try:
        first = await ingest(tmp_path, engine=engine, redis_client=client)
        assert first.docs_changed == 1
        first_version = int(await client.get("corpus:ver:about_me"))
        # A pre-model-tag index also gets one version bump during migration.
        assert first_version in {starting_version + 1, starting_version + 2}
        assert first.chunks_written == 1
        assert "corpus/about-me/unsafe.md" in first.errors
        with Session(engine) as session:
            document = session.scalar(select(Document).where(Document.source_path == source_path))
            assert document is not None
            chunks = list(
                session.scalars(select(DbChunk).where(DbChunk.document_id == document.id))
            )
            assert len(chunks) == 1
            chunk = chunks[0]
            assert chunk.text.startswith("# Fixture")
            assert len(chunk.embedding) == 2048
            redis_key = f"chunk:{chunk.id}"
            redis_keys.append(redis_key)
        assert await client.hget(redis_key, "source_path") == source_path.encode()
        assert await client.execute_command("FT.INFO", "idx:chunks")

        second = await ingest(tmp_path, engine=engine, redis_client=client)
        assert second.docs_changed == 0
        assert int(await client.get("corpus:ver:about_me")) == first_version
        assert second.chunks_written == 0
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(DbChunk)
                    .where(DbChunk.document_id == document.id)
                )
                == 1
            )

        (about_me / fixture_name).write_text("# Fixture updated\n\nNew body.\n")
        third = await ingest(tmp_path, engine=engine, redis_client=client)
        assert third.docs_changed == 1
        assert int(await client.get("corpus:ver:about_me")) == first_version + 1
        assert third.chunks_written == 1
        assert not await client.exists(redis_key)
        with Session(engine) as session:
            new_chunk = session.scalar(select(DbChunk).where(DbChunk.document_id == document.id))
            assert new_chunk.text.startswith("# Fixture updated")
            redis_keys.append(f"chunk:{new_chunk.id}")
        assert await client.exists(redis_keys[-1])
    finally:
        with engine.begin() as connection:
            document_id = connection.scalar(
                select(Document.id).where(Document.source_path == source_path)
            )
            if document_id is not None:
                connection.execute(Document.__table__.delete().where(Document.id == document_id))
            new_run_ids = set(connection.scalars(select(IngestionRun.id))) - existing_run_ids
            if new_run_ids:
                connection.execute(
                    IngestionRun.__table__.delete().where(IngestionRun.id.in_(new_run_ids))
                )
        if redis_keys:
            await client.delete(*redis_keys)
        await client.aclose()


@pytest.mark.asyncio
async def test_chunker_exception_quarantines_document_and_continues(
    tmp_path, integration_stack, monkeypatch
):
    engine, client = integration_stack
    try:
        await client.ping()
    except Exception as exc:
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")
    about_me = tmp_path / "corpus" / "about-me"
    about_me.mkdir(parents=True)
    bad_path = f"corpus/about-me/{tmp_path.name}-bad.md"
    good_path = f"corpus/about-me/{tmp_path.name}-good.md"
    (tmp_path / bad_path).write_text("# Bad\n")
    (tmp_path / good_path).write_text("# Good\n")
    original_chunker = ingest_run._CHUNKERS[".md"]

    def failing_chunker(content, source_path):
        if source_path == bad_path:
            raise IndexError("malformed document")
        return original_chunker(content, source_path)

    monkeypatch.setitem(ingest_run._CHUNKERS, ".md", failing_chunker)
    with engine.connect() as connection:
        existing_run_ids = set(connection.scalars(select(IngestionRun.id)))
    redis_keys = []
    try:
        result = await ingest(tmp_path, engine=engine, redis_client=client)
        assert result.errors[bad_path] == "malformed document"
        assert result.docs_changed == 1
        assert result.chunks_written == 1
        with Session(engine) as session:
            assert session.scalar(select(Document).where(Document.source_path == bad_path)) is None
            document = session.scalar(select(Document).where(Document.source_path == good_path))
            assert document is not None
            redis_keys = [
                f"chunk:{chunk_id}"
                for chunk_id in session.scalars(
                    select(DbChunk.id).where(DbChunk.document_id == document.id)
                )
            ]
            assert len(redis_keys) == 1
            run = session.scalar(
                select(IngestionRun).where(IngestionRun.id.not_in(existing_run_ids))
            )
            assert run.status == "succeeded"
    finally:
        with engine.begin() as connection:
            for source_path in (bad_path, good_path):
                document_id = connection.scalar(
                    select(Document.id).where(Document.source_path == source_path)
                )
                if document_id is not None:
                    connection.execute(
                        Document.__table__.delete().where(Document.id == document_id)
                    )
            new_run_ids = set(connection.scalars(select(IngestionRun.id))) - existing_run_ids
            if new_run_ids:
                connection.execute(
                    IngestionRun.__table__.delete().where(IngestionRun.id.in_(new_run_ids))
                )
        if redis_keys:
            await client.delete(*redis_keys)
        await client.aclose()


@pytest.mark.asyncio
async def test_unchanged_document_is_reembedded_when_model_changes(
    tmp_path, integration_stack, monkeypatch
):
    engine, client = integration_stack
    try:
        await client.ping()
    except Exception as exc:
        pytest.skip(f"real Redis unavailable: {exc}")
    about_me = tmp_path / "corpus" / "about-me"
    about_me.mkdir(parents=True)
    source_path = f"corpus/about-me/{tmp_path.name}.md"
    (tmp_path / source_path).write_text("# Stable content\n\nThe content does not change.\n")

    class NewModel(FakeEmbeddingProvider):
        model_id = "test-embedding-v2"

    with engine.connect() as connection:
        existing_run_ids = set(connection.scalars(select(IngestionRun.id)))
    document_id = None
    vector_keys = []
    try:
        first = await ingest(tmp_path, engine=engine, redis_client=client)
        assert first.docs_changed == 1
        with Session(engine) as session:
            document = session.scalar(select(Document).where(Document.source_path == source_path))
            document_id = document.id
            old_chunk = session.scalar(select(DbChunk).where(DbChunk.document_id == document_id))
            vector_keys.append(f"chunk:{old_chunk.id}")
            assert old_chunk.embedding_model == "fake-v1"

        monkeypatch.setattr(ingest_run, "get_embedding_provider", NewModel)
        second = await ingest(tmp_path, engine=engine, redis_client=client)
        assert second.docs_changed == 1
        with Session(engine) as session:
            new_chunk = session.scalar(select(DbChunk).where(DbChunk.document_id == document_id))
            vector_keys.append(f"chunk:{new_chunk.id}")
            assert new_chunk.id != old_chunk.id
            assert new_chunk.embedding_model == "test-embedding-v2"
            assert len(new_chunk.embedding) == 2048
        assert not await client.exists(vector_keys[0])
        assert await client.exists(vector_keys[1])

        third = await ingest(tmp_path, engine=engine, redis_client=client)
        assert third.docs_changed == 0
    finally:
        if document_id is not None:
            with engine.begin() as connection:
                connection.execute(Document.__table__.delete().where(Document.id == document_id))
                new_run_ids = set(connection.scalars(select(IngestionRun.id))) - existing_run_ids
                if new_run_ids:
                    connection.execute(
                        IngestionRun.__table__.delete().where(IngestionRun.id.in_(new_run_ids))
                    )
        if vector_keys:
            await client.delete(*vector_keys)
        await client.aclose()

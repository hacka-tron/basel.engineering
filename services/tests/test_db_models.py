from datetime import datetime

from sqlalchemy import BigInteger, Integer, String, UniqueConstraint
from sqlalchemy.dialects.mysql import BLOB, CHAR, ENUM, JSON, MEDIUMTEXT, TIMESTAMP
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from services.glassbox.db.models import Base, Chunk, Document, IngestionRun, Query
from services.glassbox.db.session import get_database_url


def test_schema_columns_and_constraints():
    assert set(Base.metadata.tables) == {"documents", "chunks", "ingestion_runs", "queries"}

    documents = Document.__table__
    assert [column.name for column in documents.columns] == [
        "id",
        "corpus",
        "source_path",
        "title",
        "content_hash",
        "commit_sha",
        "updated_at",
    ]
    assert isinstance(documents.c.id.type, BigInteger)
    assert documents.c.id.primary_key and documents.c.id.autoincrement
    assert isinstance(documents.c.corpus.type, ENUM)
    assert documents.c.corpus.type.enums == ["about_me", "about_system"]
    assert not documents.c.corpus.nullable
    assert isinstance(documents.c.source_path.type, String)
    assert documents.c.source_path.type.length == 512
    assert not documents.c.source_path.nullable
    assert documents.c.title.type.length == 512 and documents.c.title.nullable
    assert isinstance(documents.c.content_hash.type, CHAR)
    assert documents.c.content_hash.type.length == 64
    assert not documents.c.content_hash.nullable
    assert documents.c.commit_sha.type.length == 40 and documents.c.commit_sha.nullable
    assert isinstance(documents.c.updated_at.type, TIMESTAMP)
    assert str(documents.c.updated_at.server_default.arg) == (
        "CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_doc"
        and [column.name for column in constraint.columns] == ["corpus", "source_path"]
        for constraint in documents.constraints
    )

    chunks = Chunk.__table__
    assert [column.name for column in chunks.columns] == [
        "id",
        "document_id",
        "ordinal",
        "text",
        "start_line",
        "end_line",
        "token_count",
        "embedding",
        "embedding_model",
    ]
    assert isinstance(chunks.c.id.type, BigInteger)
    assert isinstance(chunks.c.document_id.type, BigInteger)
    assert isinstance(chunks.c.ordinal.type, Integer)
    assert isinstance(chunks.c.text.type, MEDIUMTEXT)
    assert isinstance(chunks.c.embedding.type, BLOB)
    assert chunks.c.embedding_model.type.length == 128
    assert all(
        not chunks.c[name].nullable
        for name in ("document_id", "ordinal", "text", "embedding", "embedding_model")
    )
    assert all(chunks.c[name].nullable for name in ("start_line", "end_line", "token_count"))
    fk = next(iter(chunks.c.document_id.foreign_keys))
    assert fk.target_fullname == "documents.id" and fk.ondelete == "CASCADE"
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_chunk"
        and [column.name for column in constraint.columns] == ["document_id", "ordinal"]
        for constraint in chunks.constraints
    )

    runs = IngestionRun.__table__
    assert [column.name for column in runs.columns] == [
        "id",
        "commit_sha",
        "started_at",
        "finished_at",
        "docs_changed",
        "chunks_written",
        "status",
        "notes",
    ]
    assert isinstance(runs.c.id.type, BigInteger)
    assert runs.c.commit_sha.type.length == 40 and runs.c.commit_sha.nullable
    assert isinstance(runs.c.started_at.type, TIMESTAMP) and not runs.c.started_at.nullable
    assert isinstance(runs.c.finished_at.type, TIMESTAMP) and runs.c.finished_at.nullable
    assert all(
        isinstance(runs.c[name].type, Integer) for name in ("docs_changed", "chunks_written")
    )
    assert all(
        str(runs.c[name].server_default.arg) == "0" for name in ("docs_changed", "chunks_written")
    )
    assert isinstance(runs.c.status.type, ENUM)
    assert runs.c.status.type.enums == ["running", "succeeded", "failed"]
    assert not runs.c.status.nullable

    queries = Query.__table__
    assert [column.name for column in queries.columns] == [
        "id",
        "request_id",
        "corpus",
        "question",
        "cache_status",
        "mode",
        "chunk_ids",
        "stage_timings_ms",
        "total_ms",
        "tokens_in",
        "tokens_out",
        "created_at",
        "turn_index",
        "rewritten_query",
        "ttft_ms",
    ]
    assert isinstance(queries.c.id.type, BigInteger)
    assert queries.c.id.primary_key and queries.c.id.autoincrement
    assert isinstance(queries.c.request_id.type, CHAR)
    assert queries.c.request_id.type.length == 26
    assert isinstance(queries.c.corpus.type, ENUM)
    assert queries.c.corpus.type.enums == ["about_me", "about_system"]
    assert isinstance(queries.c.question.type, String)
    assert queries.c.question.type.length == 1000
    assert isinstance(queries.c.cache_status.type, ENUM)
    assert queries.c.cache_status.type.enums == ["answer_hit", "miss"]
    assert isinstance(queries.c.mode.type, ENUM)
    assert queries.c.mode.type.enums == ["full", "retrieval_only", "stopped"]
    assert all(
        not queries.c[name].nullable
        for name in ("request_id", "corpus", "question", "cache_status", "mode")
    )
    assert all(isinstance(queries.c[name].type, JSON) for name in ("chunk_ids", "stage_timings_ms"))
    assert all(
        isinstance(queries.c[name].type, Integer)
        for name in ("total_ms", "tokens_in", "tokens_out")
    )
    assert all(
        queries.c[name].nullable
        for name in ("chunk_ids", "stage_timings_ms", "total_ms", "tokens_in", "tokens_out")
    )
    assert isinstance(queries.c.created_at.type, TIMESTAMP)
    assert queries.c.created_at.nullable
    assert str(queries.c.created_at.server_default.arg) == "CURRENT_TIMESTAMP"
    assert not queries.c.turn_index.nullable
    assert str(queries.c.turn_index.server_default.arg) == "0"
    assert queries.c.rewritten_query.nullable
    assert queries.c.rewritten_query.type.length == 1000
    assert isinstance(queries.c.ttft_ms.type, Integer)
    assert queries.c.ttft_ms.nullable and queries.c.ttft_ms.server_default is None
    assert not queries.foreign_keys
    assert len(queries.indexes) == 1
    index = next(iter(queries.indexes))
    assert index.name == "idx_created"
    assert [column.name for column in index.columns] == ["created_at"]


def test_models_construct_without_database():
    document = Document(corpus="about_me", source_path="bio.md", content_hash="a" * 64)
    chunk = Chunk(
        document_id=1,
        ordinal=0,
        text="hello",
        embedding=b"\x00" * 2048,
        embedding_model="test-model",
    )
    run = IngestionRun(started_at=datetime(2026, 9, 29), status="running")
    query = Query(
        request_id="01K6AA0XX5S4PX8J2C1W39VQ9M",
        corpus="about_me",
        question="Who is Basel?",
        cache_status="miss",
        mode="full",
        chunk_ids=[1, 2],
        stage_timings_ms={"retrieval": 12},
    )

    assert document.source_path == "bio.md"
    assert chunk.embedding == b"\x00" * 2048
    assert run.status == "running"
    assert query.chunk_ids == [1, 2]
    assert query.stage_timings_ms == {"retrieval": 12}


def test_mysql_ddl_contains_required_schema_clauses():
    ddl = {
        name: str(CreateTable(table).compile(dialect=mysql_dialect()))
        for name, table in Base.metadata.tables.items()
    }
    assert set(ddl) == {"documents", "chunks", "ingestion_runs", "queries"}
    assert "AUTO_INCREMENT" in ddl["documents"]
    assert "ENUM('about_me','about_system')" in ddl["documents"]
    assert "CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in ddl["documents"]
    assert "uq_doc" in ddl["documents"]
    assert "MEDIUMTEXT" in ddl["chunks"]
    assert "BLOB" in ddl["chunks"]
    assert "ON DELETE CASCADE" in ddl["chunks"]
    assert "uq_chunk" in ddl["chunks"]
    assert "ENUM('running','succeeded','failed')" in ddl["ingestion_runs"]
    assert "DEFAULT 0" in ddl["ingestion_runs"]
    assert "AUTO_INCREMENT" in ddl["queries"]
    assert "CHAR(26) NOT NULL" in ddl["queries"]
    assert "ENUM('about_me','about_system') NOT NULL" in ddl["queries"]
    assert "VARCHAR(1000) NOT NULL" in ddl["queries"]
    assert "ENUM('answer_hit','miss') NOT NULL" in ddl["queries"]
    assert "ENUM('full','retrieval_only','stopped') NOT NULL" in ddl["queries"]
    assert ddl["queries"].count("JSON") == 2
    assert "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP" in ddl["queries"]
    # DESIGN-002 §9.3 conversational columns.
    assert "turn_index TINYINT UNSIGNED NOT NULL DEFAULT 0" in ddl["queries"]
    assert "rewritten_query VARCHAR(1000)," in ddl["queries"]
    assert "ttft_ms INTEGER," in ddl["queries"]
    index = next(iter(Query.__table__.indexes))
    assert str(CreateIndex(index).compile(dialect=mysql_dialect())) == (
        "CREATE INDEX idx_created ON queries (created_at)"
    )


def test_database_url_uses_required_environment_and_default_port(monkeypatch):
    for key in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("MYSQL_HOST", "db.example")
    monkeypatch.setenv("MYSQL_USER", "glassbox")
    monkeypatch.setenv("MYSQL_PASSWORD", "p@ss word")
    monkeypatch.setenv("MYSQL_DATABASE", "glassbox")
    monkeypatch.delenv("MYSQL_PORT", raising=False)

    url = get_database_url()
    assert url.drivername == "mysql+pymysql"
    assert url.host == "db.example"
    assert url.port == 3306
    assert url.username == "glassbox"
    assert url.password == "p@ss word"
    assert url.database == "glassbox"

    monkeypatch.setenv("MYSQL_PORT", "3307")
    assert get_database_url().port == 3307


def test_conversation_columns_migration_follows_queries_table():
    import importlib

    migration = importlib.import_module(
        "services.glassbox.db.migrations.versions.0003_query_turn_columns"
    )
    assert migration.down_revision == "0002_add_queries_table"
    assert migration.revision == "0003_query_turn_columns"
    # alembic_version.version_num is VARCHAR(32).
    assert len(migration.revision) <= 32


def test_stopped_mode_migration_follows_conversation_columns():
    import importlib

    migration = importlib.import_module(
        "services.glassbox.db.migrations.versions.0004_query_mode_stopped"
    )
    assert migration.down_revision == "0003_query_turn_columns"
    assert migration.revision == "0004_query_mode_stopped"
    assert len(migration.revision) <= 32


def test_ttft_migration_follows_stopped_mode_and_is_additive():
    import importlib
    import inspect

    migration = importlib.import_module(
        "services.glassbox.db.migrations.versions.0005_query_ttft_ms"
    )
    assert migration.down_revision == "0004_query_mode_stopped"
    assert migration.revision == "0005_query_ttft_ms"
    assert len(migration.revision) <= 32
    # Safe during a rolling release: one nullable column added, nothing altered.
    upgrade = inspect.getsource(migration.upgrade)
    assert "add_column" in upgrade and "nullable=True" in upgrade
    assert "alter_column" not in upgrade and "drop_column" not in upgrade


def test_ingestion_run_notes_migration_follows_ttft_and_is_additive():
    import importlib
    import inspect

    migration = importlib.import_module(
        "services.glassbox.db.migrations.versions.0006_ingestion_run_notes"
    )
    assert migration.down_revision == "0005_query_ttft_ms"
    assert migration.revision == "0006_ingestion_run_notes"
    assert len(migration.revision) <= 32
    upgrade = inspect.getsource(migration.upgrade)
    assert "add_column" in upgrade and "nullable=True" in upgrade
    assert "alter_column" not in upgrade and "drop_column" not in upgrade

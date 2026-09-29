from datetime import datetime

from sqlalchemy import BigInteger, Integer, String, UniqueConstraint
from sqlalchemy.dialects.mysql import BLOB, CHAR, ENUM, MEDIUMTEXT, TIMESTAMP
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.schema import CreateTable

from services.glassbox.db.models import Base, Chunk, Document, IngestionRun
from services.glassbox.db.session import get_database_url


def test_schema_columns_and_constraints():
    assert set(Base.metadata.tables) == {"documents", "chunks", "ingestion_runs"}

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

    assert document.source_path == "bio.md"
    assert chunk.embedding == b"\x00" * 2048
    assert run.status == "running"


def test_mysql_ddl_contains_required_schema_clauses():
    ddl = {
        name: str(CreateTable(table).compile(dialect=mysql_dialect()))
        for name, table in Base.metadata.tables.items()
    }
    assert set(ddl) == {"documents", "chunks", "ingestion_runs"}
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

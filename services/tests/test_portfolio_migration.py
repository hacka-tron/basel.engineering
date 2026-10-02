"""Migration 0007 on a real MySQL: both corpus columns take 'portfolio'.

CI runs ``alembic upgrade head`` before pytest, so an unmigrated column fails there;
locally (no MySQL, or an old schema) these tests skip.
"""

import os

import pytest
from sqlalchemy import delete, select

from services.glassbox.db.models import Document, Query
from services.glassbox.db.session import create_db_engine, get_session_factory

TEST_MYSQL_PORT = os.environ.get("GLASSBOX_TEST_MYSQL_PORT", "3306")
EXPECTED = "enum('about_me','about_system','portfolio')"


@pytest.fixture
def engine(monkeypatch):
    for key, value in {
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": TEST_MYSQL_PORT,
        "MYSQL_USER": "glassbox",
        "MYSQL_PASSWORD": "glassbox",  # pragma: allowlist secret (throwaway test DB)
        "MYSQL_DATABASE": "glassbox",
    }.items():
        monkeypatch.setenv(key, value)
    get_session_factory.cache_clear()
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:  # pragma: no cover - depends on local services
        engine.dispose()
        pytest.skip(f"MySQL unavailable: {exc}")
    yield engine
    engine.dispose()
    get_session_factory.cache_clear()


def _corpus_type(engine, table: str) -> str:
    with engine.connect() as connection:
        raw = connection.exec_driver_sql(f"SHOW COLUMNS FROM {table} LIKE 'corpus'").one()[1]
    return raw.decode() if isinstance(raw, bytes) else raw


def _require_migrated(engine) -> None:
    for table in ("documents", "queries"):
        if "'portfolio'" not in _corpus_type(engine, table):
            message = f"MySQL {table}.corpus is not migrated to 0007"
            if os.environ.get("CI"):
                pytest.fail(message)
            pytest.skip(message)


@pytest.mark.parametrize("table", ["documents", "queries"])
def test_portfolio_is_appended_to_the_corpus_enum(engine, table):
    _require_migrated(engine)
    assert _corpus_type(engine, table) == EXPECTED


def test_portfolio_documents_and_queries_are_storable(engine):
    _require_migrated(engine)
    path = "corpus/portfolio/migration-test.md"
    request_id = "01PORTFOLIOMIGRATIONTEST00"
    sessions = get_session_factory()
    try:
        with sessions() as session:
            session.add(
                Document(corpus="portfolio", source_path=path, content_hash="a" * 64, title="Test")
            )
            session.add(
                Query(
                    request_id=request_id,
                    corpus="portfolio",
                    question="What can Basel build for me?",
                    cache_status="miss",
                    mode="full",
                )
            )
            session.commit()
            stored = session.scalar(select(Document.corpus).where(Document.source_path == path))
            assert stored == "portfolio"
            logged = session.scalar(select(Query.corpus).where(Query.request_id == request_id))
            assert logged == "portfolio"
    finally:
        with sessions() as session:
            session.execute(
                delete(Document).where(Document.source_path == path, Document.corpus == "portfolio")
            )
            session.execute(delete(Query).where(Query.request_id == request_id))
            session.commit()

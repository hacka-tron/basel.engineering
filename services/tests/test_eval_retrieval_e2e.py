"""Retrieval eval v2 end to end: fake ingest into real MySQL/Redis, then ``evaluate``.

Skips without the integration stack (CI provides it); the metric math is covered
without Redis in ``test_eval.py``.
"""

import pytest
import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from eval import run_eval
from services.glassbox.db.models import Base, Document, IngestionRun
from services.glassbox.db.models import Chunk as DbChunk
from services.glassbox.db.session import create_db_engine
from services.glassbox.ingest.run import ingest


@pytest.fixture
def integration_stack(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_USER", "glassbox")
    monkeypatch.setenv("MYSQL_PASSWORD", "glassbox")
    monkeypatch.setenv("MYSQL_DATABASE", "glassbox")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("GLASSBOX_PROVIDER", "fake")
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.mark.asyncio
async def test_evaluate_scores_k8_chunk_level_and_noise_against_real_index(
    tmp_path, integration_stack, monkeypatch
):
    engine = integration_stack
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")
    name = tmp_path.name.replace("-", "_")
    about_me = tmp_path / "corpus" / "about-me-private" / "about-me"
    about_me.mkdir(parents=True)
    tests_dir = tmp_path / "services" / "tests"
    tests_dir.mkdir(parents=True)
    doc_path = f"private/{name}.md"
    noise_path = f"services/tests/test_{name}.py"
    (about_me / f"{name}.md").write_text(f"# Fixture {name}\n\nGold phrase {name} here.\n")
    (tests_dir / f"test_{name}.py").write_text(f"def test_{name}():\n    return 'noise'\n")
    with engine.connect() as connection:
        existing_run_ids = set(connection.scalars(select(IngestionRun.id)))
    redis_keys = []
    try:
        await ingest(tmp_path, engine=engine, redis_client=client)
        with Session(engine) as session:
            texts = {}
            for path in (doc_path, noise_path):
                rows = session.execute(
                    select(DbChunk.id, DbChunk.text)
                    .join(Document, DbChunk.document_id == Document.id)
                    .where(Document.source_path == path)
                ).all()
                assert len(rows) == 1
                redis_keys.append(f"chunk:{rows[0].id}")
                texts[path] = rows[0].text
        # Fake embeddings are hashes of the exact text, so a question equal to a
        # chunk's text retrieves that chunk first. Skip whitespace normalization.
        monkeypatch.setattr(run_eval, "normalize_question", lambda text: text)
        questions = [
            {
                "id": "doc",
                "corpus": "about_me",
                "category": "fact",
                "question": texts[doc_path],
                "expected_sources": [doc_path],
                "gold_snippets": [f"gold  PHRASE {name}"],
            },
            {
                "id": "noisy",
                "corpus": "about_system",
                "category": "live",
                "question": texts[noise_path],
                "expected_sources": [noise_path],
                "gold_snippets": [f"absent snippet {name}"],
            },
        ]
        result = await run_eval.evaluate(questions)
    finally:
        with engine.begin() as connection:
            document_ids = list(
                connection.scalars(
                    select(Document.id).where(Document.source_path.in_([doc_path, noise_path]))
                )
            )
            if document_ids:
                connection.execute(Document.__table__.delete().where(Document.id.in_(document_ids)))
            new_run_ids = set(connection.scalars(select(IngestionRun.id))) - existing_run_ids
            if new_run_ids:
                connection.execute(
                    IngestionRun.__table__.delete().where(IngestionRun.id.in_(new_run_ids))
                )
        if redis_keys:
            await client.delete(*redis_keys)
        await client.aclose()

    assert result["top_k"] == 8 and result["eval_version"] == 2
    cases = {case["id"]: case for case in result["cases"]}
    doc, noisy = cases["doc"], cases["noisy"]
    assert doc["retrieved_at_8"][0]["source"] == doc_path
    assert (doc["hit"], doc["reciprocal_rank"], doc["hit_at_8"]) == (1, 1.0, 1)
    assert (doc["chunk_hit"], doc["chunk_reciprocal_rank"]) == (1, 1.0)
    assert noisy["retrieved_at_8"][0]["source"] == noise_path
    assert noisy["noise_count"] >= 1
    assert noisy["chunk_hit"] == 0
    assert result["unreachable_gold_snippets"] == ["noisy"]
    overall = result["overall"]
    assert overall["chunk_count"] == 2 and overall["chunk_recall_at_8"] == 0.5
    assert 0 < overall["noise_at_8"] <= 1
    assert set(result["by_category"]) == {"fact", "live"}

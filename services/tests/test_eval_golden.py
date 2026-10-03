"""Golden dataset v2 schema and the in-process answer runner (eval/run_answers.py)."""

import asyncio
import copy
import json
import os
from collections import Counter

import pytest
import yaml

from eval import run_answers
from eval.run_eval import QUESTIONS_PATH, load_questions
from eval.schema import GOLDEN_PATH, GoldenError, load_golden, snippet_in, validate_case
from services.glassbox.api.ask import WorkerChunk
from services.glassbox.providers.base import ABSTENTION_ANSWER
from services.glassbox.providers.fake import FakeEmbeddingProvider, FakeLLMProvider


def _disk(root, source_path):
    """Where a ``private/<name>.md`` source path lives in a fixture checkout under ``root``."""
    return root / "corpus" / "about-me-private" / "about-me" / source_path.removeprefix("private/")


def test_committed_golden_set_is_valid():
    # load_golden checks ids, categories, regexes, history shape, and that every
    # gold snippet occurs in one of its expected source files in this repo.
    cases = load_golden()
    counts = Counter(case["category"] for case in cases)
    assert 65 <= len(cases) <= 95
    assert counts["fact"] >= 35
    assert counts["planned"] >= 8
    assert counts["live"] >= 5
    assert counts["unanswerable"] >= 10
    assert counts["multi_turn"] >= 8
    assert counts["injection"] >= 4
    holdout = sum(bool(case.get("holdout")) for case in cases)
    assert 0.15 <= holdout / len(cases) <= 0.25


def test_questions_yaml_cases_migrate_verbatim():
    golden = {case["id"]: case for case in load_golden()}
    # Explicit path: load_questions() prefers golden.yaml once it exists.
    for question in load_questions(QUESTIONS_PATH):
        case = golden[question["id"]]
        assert case["origin"] == "questions.yaml"
        for key in ("corpus", "question", "expected_sources"):
            assert case[key] == question[key], (question["id"], key)


def test_suggested_questions_are_covered():
    repo_root = GOLDEN_PATH.parent.parent
    suggested = json.loads((repo_root / "frontend/src/suggested-questions.json").read_text())
    golden = {(case["corpus"], case["question"]) for case in load_golden()}
    for corpus, questions in suggested.items():
        if corpus == "portfolio":
            # No Portfolio golden cases until the owner adds real projects (owner rule:
            # no golden-set additions or RAG evaluations before then; BACKLOG).
            continue
        for question in questions:
            assert (corpus, question) in golden, question


def test_stress_test_case_requires_the_facts_the_thin_answer_dropped():
    case = next(case for case in load_golden() if case["id"] == "sugg-system-stress")
    assert any("512" in pattern for pattern in case["must_include"])
    assert any("min" in pattern for pattern in case["must_include"])


def test_snippet_match_ignores_case_and_line_wraps():
    assert snippet_in("A capacity\n  gate at 512 MiB.", "capacity gate AT 512 mib")
    assert not snippet_in("A capacity gate", "capacity gate at 512 MiB")


def _valid_case() -> dict:
    return copy.deepcopy(next(case for case in load_golden() if case["id"] == "mt-cache-ttl"))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c.update(category="opinion"), "unknown category"),
        (lambda c: c.update(corpus="about_you"), "unknown corpus"),
        (lambda c: c.update(must_include=["(unclosed"]), "does not compile"),
        (lambda c: c.update(gold_snippets=["text that is not in the file"]), "not found"),
        (lambda c: c.update(expected_sources=["docs/missing.md"]), "does not exist"),
        (lambda c: c["history"].reverse(), "alternate"),
        (lambda c: c["history"].pop(), "end with an assistant"),
        (lambda c: c.update(history=[]), "need history"),
        (lambda c: c.update(surprise=True), "unknown keys"),
        (lambda c: c.update(known_failure=""), "known_failure"),
        (lambda c: c.update(known_failure=3), "known_failure"),
        (lambda c: c.update(live_but_off=True), "only for live cases"),
    ],
)
def test_schema_rejects_malformed_cases(mutate, message):
    case = _valid_case()
    mutate(case)
    with pytest.raises(GoldenError, match=message):
        validate_case(case)


def test_schema_rejects_duplicate_ids_and_unreviewed_about_basel(tmp_path):
    cases = load_golden()
    duplicate = tmp_path / "golden.yaml"
    duplicate.write_text(yaml.safe_dump({"version": 2, "cases": [cases[0], cases[0]]}))
    with pytest.raises(GoldenError, match="duplicate"):
        load_golden(duplicate)
    about_me = copy.deepcopy(next(case for case in cases if case["corpus"] == "about_me"))
    about_me.pop("needs_owner_review")
    with pytest.raises(GoldenError, match="needs_owner_review"):
        validate_case(about_me)


def _stub_retriever(calls: list):
    async def retrieve(vector, corpus, model_id):
        calls.append((corpus, model_id, len(vector)))
        if corpus == "about_me":
            return []  # exercises the no-sources abstention path
        return [
            WorkerChunk(
                n=1,
                chunk_id=7,
                text="A real burst needs 512 MiB free and has a 5-minute cooldown.",
                source_path="docs/architecture/deep-dive.md",
                title="Glassbox architecture deep dive",
                score=0.81,
            )
        ]

    return retrieve


def test_run_answers_produces_one_graded_row_per_case_with_the_fake_provider():
    cases = load_golden()
    calls: list = []
    rows = asyncio.run(
        run_answers.run_cases(
            cases,
            embedder=FakeEmbeddingProvider(),
            llm=FakeLLMProvider(),
            retrieve=_stub_retriever(calls),
        )
    )
    assert [row["id"] for row in rows] == [case["id"] for case in cases]
    assert len(calls) == len(cases)
    assert all(row["error"] is None and row["grades"] for row in rows)
    by_id = {row["id"]: row for row in rows}
    # No sources: the canonical abstention without an LLM call, like the API.
    assert by_id["me-education"]["answer"] == ABSTENTION_ANSWER
    assert by_id["me-education"]["grades"]["failures"] == ["false_abstain", "missing_facts"]
    assert by_id["unans-me-salary"]["passed"]
    system = by_id["sugg-system-stress"]
    assert system["answer"] == "This is a fake response for local development."
    assert system["retrieved"][0]["source_path"] == "docs/architecture/deep-dive.md"
    assert system["prompt_version"].startswith("v")
    # The fake rewrite is an identity rewrite, so it misses the resolved entity.
    follow_up = by_id["mt-stress-cooldown"]
    assert follow_up["rewrite"] == "How long is the cooldown?"
    assert follow_up["grades"]["rewrite_ok"] is False

    summary = run_answers.summarize(rows)
    known = [c["id"] for c in cases if c.get("known_failure")]
    assert summary["overall"]["count"] == len(cases) - len(known)
    assert [item["id"] for item in summary["known_failures"]] == sorted(known)
    assert set(summary["by_category"]) == {
        "fact",
        "planned",
        "live",
        "unanswerable",
        "multi_turn",
        "injection",
    }
    # The stub returns no About Basel sources (abstain) but returns About This System
    # sources, where the fake model answers: 7 of the 12 unanswerable cases abstain.
    assert summary["by_category"]["unanswerable"]["abstain_rate_unanswerable"] == pytest.approx(
        7 / 12, abs=0.001
    )
    assert summary["holdout"]["count"] == sum(
        bool(c.get("holdout")) and not c.get("known_failure") for c in cases
    )
    json.dumps(summary)


def test_a_failing_case_is_recorded_and_the_run_continues():
    async def broken(vector, corpus, model_id):
        raise RuntimeError("redis down")

    cases = load_golden()[:2]
    rows = asyncio.run(
        run_answers.run_cases(
            cases, embedder=FakeEmbeddingProvider(), llm=FakeLLMProvider(), retrieve=broken
        )
    )
    assert [row["error"] for row in rows] == ["RuntimeError: redis down"] * 2
    assert not any(row["passed"] for row in rows)
    assert run_answers.summarize(rows)["overall"]["errors"] == 2


def test_known_failures_are_reported_apart_and_kept_out_of_rates():
    cases = [c for c in load_golden() if c["id"] in {"me-site-stack", "me-education"}]
    # No committed case is a known failure today, so mark one for this test.
    next(c for c in cases if c["id"] == "me-site-stack")["known_failure"] = (
        'BACKLOG "About Basel corpus error: RDS (owner sign-off)"'
    )
    rows = asyncio.run(
        run_answers.run_cases(
            cases,
            embedder=FakeEmbeddingProvider(),
            llm=FakeLLMProvider(),
            retrieve=_stub_retriever([]),
        )
    )
    summary = run_answers.summarize(rows)
    assert summary["overall"]["count"] == 1
    assert summary["failed"] == ["me-education"]
    assert summary["known_failures"] == [
        {
            "id": "me-site-stack",
            "reference": 'BACKLOG "About Basel corpus error: RDS (owner sign-off)"',
            "passed": False,
        }
    ]


def test_case_filters():
    cases = load_golden()
    picked = run_answers.select_cases(cases, categories=["planned"], max_cases=3)
    assert [case["category"] for case in picked] == ["planned"] * 3
    assert [c["id"] for c in run_answers.select_cases(cases, ids=["me-cpu"])] == ["me-cpu"]
    with pytest.raises(SystemExit):
        run_answers.select_cases(cases, ids=["nope"])
    with pytest.raises(SystemExit):
        run_answers.select_cases(cases, categories=["nope"])


@pytest.mark.parametrize(
    ("argv", "allow_env"),
    [
        (["--max-cases", "1"], None),
        (["--max-cases", "1"], "1"),
        (["--paid"], None),
        (["--paid"], "yes"),
    ],
)
def test_bedrock_needs_both_paid_flag_and_env(monkeypatch, argv, allow_env):
    monkeypatch.setenv("GLASSBOX_PROVIDER", "bedrock")
    if allow_env is None:
        monkeypatch.delenv("GLASSBOX_EVAL_ALLOW_PAID", raising=False)
    else:
        monkeypatch.setenv("GLASSBOX_EVAL_ALLOW_PAID", allow_env)
    monkeypatch.setattr(
        run_answers, "_run_against_stack", lambda cases: pytest.fail("must not run")
    )
    with pytest.raises(run_answers.PaidRunRefused, match="GLASSBOX_EVAL_ALLOW_PAID=1"):
        run_answers.main(argv)


def test_paid_guard_allows_fake_and_fully_authorized_runs(monkeypatch):
    monkeypatch.setenv("GLASSBOX_PROVIDER", "bedrock")
    monkeypatch.setenv("GLASSBOX_EVAL_ALLOW_PAID", "1")
    run_answers.check_paid_allowed(paid=True)
    monkeypatch.setenv("GLASSBOX_PROVIDER", "fake")
    monkeypatch.delenv("GLASSBOX_EVAL_ALLOW_PAID")
    run_answers.check_paid_allowed(paid=False)


def test_rewrite_failure_falls_back_to_the_original_question():
    class RewriteFails(FakeLLMProvider):
        async def generate(self, prompt, *, max_tokens, system=None):
            if system is not None and "rewrite" in system:
                raise RuntimeError("throttled")
            async for part in super().generate(prompt, max_tokens=max_tokens, system=system):
                yield part

    embedded: list[str] = []

    class RecordingEmbedder(FakeEmbeddingProvider):
        async def embed(self, texts):
            embedded.extend(texts)
            return await super().embed(texts)

    case = next(case for case in load_golden() if case["id"] == "mt-stress-cooldown")
    [row] = asyncio.run(
        run_answers.run_cases(
            [case], embedder=RecordingEmbedder(), llm=RewriteFails(), retrieve=_stub_retriever([])
        )
    )
    assert row["error"] is None
    assert row["rewrite"] is None
    assert row["rewrite_error"] == "RuntimeError: throttled"
    assert embedded == ["how long is the cooldown?"]
    assert row["grades"]["rewrite_ok"] is False
    assert row["answer"] == "This is a fake response for local development."


def test_main_writes_jsonl_and_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("GLASSBOX_PROVIDER", "fake")

    async def fake_stack(cases):
        return await run_answers.run_cases(
            cases,
            embedder=FakeEmbeddingProvider(),
            llm=FakeLLMProvider(),
            retrieve=_stub_retriever([]),
        )

    monkeypatch.setattr(run_answers, "_run_against_stack", fake_stack)
    out = tmp_path / "run.jsonl"
    assert run_answers.main(["--category", "live,planned", "--out", str(out)]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert {row["category"] for row in rows} == {"live", "planned"}
    summary = json.loads(out.with_suffix(".summary.json").read_text())
    assert summary["overall"]["count"] == len(rows)


@pytest.mark.asyncio
async def test_run_answers_end_to_end_against_mysql_and_redis(tmp_path, monkeypatch):
    """Real Redis KNN and MySQL chunk loads with fake providers (the CI services)."""
    import redis.asyncio as redis
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from services.glassbox.db.models import Base, Document
    from services.glassbox.db.models import Chunk as DbChunk
    from services.glassbox.db.session import create_db_engine
    from services.glassbox.ingest.run import ingest

    for key, value in {
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": os.environ.get("GLASSBOX_TEST_MYSQL_PORT", "3306"),
        "MYSQL_USER": "glassbox",
        "MYSQL_PASSWORD": "glassbox",  # pragma: allowlist secret (throwaway test DB)
        "MYSQL_DATABASE": "glassbox",
    }.items():
        monkeypatch.setenv(key, os.environ.get(key, value))
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    engine = create_db_engine()
    client = redis.from_url(redis_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        await client.ping()
    except Exception as exc:
        engine.dispose()
        await client.aclose()
        pytest.skip(f"real MySQL/Redis integration stack unavailable: {exc}")
    Base.metadata.create_all(engine)
    about_me = tmp_path / "corpus" / "about-me-private" / "about-me"
    about_me.mkdir(parents=True)
    source_path = f"private/{tmp_path.name}-golden.md"
    _disk(tmp_path, source_path).write_text("# Fixture\n\nBasel studied at Ohio State.\n")
    try:
        await ingest(tmp_path, engine=engine, redis_client=client)
        cases = [case for case in load_golden() if case["corpus"] == "about_me"][:3]
        rows = await run_answers.run_cases(
            cases,
            embedder=FakeEmbeddingProvider(),
            llm=FakeLLMProvider(),
            retrieve=run_answers.stack_retriever(client, sessionmaker(bind=engine)),
        )
        assert len(rows) == len(cases)
        assert all(row["error"] is None for row in rows), [row["error"] for row in rows]
        assert all(row["retrieved"] for row in rows)
        assert all(len(row["retrieved"]) <= run_answers.RETRIEVAL_TOP_K for row in rows)
    finally:
        with engine.begin() as connection:
            chunk_ids = list(
                connection.scalars(
                    select(DbChunk.id)
                    .join(Document, DbChunk.document_id == Document.id)
                    .where(Document.source_path == source_path)
                )
            )
            connection.execute(
                Document.__table__.delete().where(Document.source_path == source_path)
            )
        if chunk_ids:
            await client.delete(*(f"chunk:{chunk_id}" for chunk_id in chunk_ids))
        await client.aclose()
        engine.dispose()

"""Answer log (RAG plan phase 10): masking, retention, error counting, one row per answer."""

import asyncio
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from services.glassbox import answer_log
from services.glassbox.api import ask as _ask_module
from services.glassbox.api.main import app
from services.glassbox.db.models import Query
from services.tests.test_ask_endpoint import MemoryRedis, events
from services.tests.test_ask_history import AllowAllRate

ORIGINAL_SAVE_QUERY = _ask_module._save_query
TEST_MYSQL_PORT = os.environ.get("GLASSBOX_TEST_MYSQL_PORT", "3306")


@pytest.fixture(autouse=True)
def fresh_stats(monkeypatch):
    monkeypatch.setattr(answer_log, "STATS", answer_log.Counter())
    monkeypatch.setattr(answer_log, "_last_purge", None)


def _fields(**overrides):
    fields = dict(
        request_id="01ANSWERLOGUNIT00000000000",
        corpus="about_me",
        question="What did you build?",
        chunk_ids=[1, 2],
        timings={"llm": 5},
        total_ms=100,
        tokens_in=10,
        tokens_out=5,
        turn_index=0,
        rewritten_query=None,
        ttft_ms=40,
        cache_status="miss",
        mode="full",
        answer="I built this site.",
        abstained=False,
        route="strict",
        llm_model_id="fake-llm-v1",
        prompt_version="v18",
    )
    fields.update(overrides)
    return fields


class RecordingSession:
    def __init__(self, *, fail_commit=False, rowcount=0):
        self.rows = []
        self.executed = []
        self.commits = 0
        self.fail_commit = fail_commit
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def add(self, row):
        self.rows.append(row)

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return type("Result", (), {"rowcount": self.rowcount})()

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("database down")
        self.commits += 1


def _factory(session):
    """Stands in for db.session.get_session_factory."""
    return lambda: lambda: session


# --- masking ------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        "Email me at jane.doe@example.com about a role",
        "Call me on (614) 555-0100 please",
        "My number is +44 20 7946 0958",
        "my ssn is 123-45-6789",
    ],
)
def test_visitor_personal_data_is_masked(typed):
    masked = answer_log.mask_text(typed, 1000)
    assert "[redacted]" in masked
    for secret in ("jane.doe@example.com", "555-0100", "7946 0958", "123-45-6789"):
        assert secret not in masked


def test_plain_questions_and_dates_are_kept():
    question = "Did you ship Kubernetes in 2024-05? What was the p95 latency, 120 ms?"
    assert answer_log.mask_text(question, 1000) == question
    assert answer_log.mask_text(None, 1000) is None


def test_masking_cannot_overflow_the_column():
    masked = answer_log.mask_text("a@b.co " * 200, answer_log.QUESTION_MAX_CHARS)
    assert len(masked) == answer_log.QUESTION_MAX_CHARS


def test_build_row_masks_question_rewrite_and_answer():
    row = answer_log.build_row(
        **_fields(
            question="I'm jane@example.com, are you free?",
            rewritten_query="Is Basel free? Contact jane@example.com",
            answer="Sure, I'll write to jane@example.com.",
        )
    )
    for value in (row.question, row.rewritten_query, row.answer):
        assert "jane@example.com" not in value and "[redacted]" in value


def test_build_row_records_generation_metadata_only_for_model_answers():
    row = answer_log.build_row(**_fields(route="casual"))
    assert (row.answer_route, row.llm_model_id, row.prompt_version) == (
        "casual",
        "fake-llm-v1",
        "v18",
    )
    # The no-sources abstention is canned text, not a model answer.
    canned = answer_log.build_row(**_fields(route=None, abstained=True))
    assert canned.abstained is True
    assert (canned.answer_route, canned.llm_model_id, canned.prompt_version) == (None,) * 3
    # Retrieval-only (kill switch, budget) rows carry no answer at all.
    retrieval_only = answer_log.build_row(
        **_fields(mode="retrieval_only", answer=None, abstained=None)
    )
    assert retrieval_only.answer is None and retrieval_only.prompt_version is None


# --- retention ----------------------------------------------------------------


@pytest.mark.parametrize(("raw", "days"), [("", 90), ("30", 30), ("0", 1), ("-5", 1), ("soon", 90)])
def test_retention_days_from_environment(monkeypatch, raw, days):
    monkeypatch.setenv("GLASSBOX_QUERY_LOG_RETENTION_DAYS", raw)
    assert answer_log.retention_days() == days


def test_purge_deletes_a_bounded_batch_of_old_rows():
    session = RecordingSession(rowcount=3)
    assert answer_log.purge_expired(session, days=90) == 3
    [(sql, params)] = session.executed
    assert sql.startswith("DELETE FROM queries WHERE created_at < NOW() - INTERVAL :days DAY")
    assert "LIMIT :batch" in sql
    assert params == {"days": 90, "batch": answer_log.PURGE_BATCH}
    assert session.commits == 1


def test_purge_runs_at_most_once_an_hour_per_process():
    session = RecordingSession(rowcount=2)
    factory = _factory(session)
    assert answer_log.maybe_purge(factory, now=1000.0) == 2
    assert answer_log.maybe_purge(factory, now=1000.0 + 3599) == 0
    assert answer_log.maybe_purge(factory, now=1000.0 + 3600) == 2
    assert len(session.executed) == 2
    assert answer_log.STATS["purged"] == 4


def test_purge_failure_is_swallowed_and_counted():
    session = RecordingSession(fail_commit=True)
    assert answer_log.maybe_purge(_factory(session), now=5.0) == 0
    assert answer_log.STATS["purge_failures"] == 1


# --- writing ------------------------------------------------------------------


def test_record_writes_one_row_then_purges():
    session = RecordingSession()
    assert answer_log.record(_factory(session), **_fields()) is True
    [row] = session.rows
    assert row.answer == "I built this site." and row.abstained is False
    assert session.executed and "DELETE FROM queries" in session.executed[0][0]
    assert answer_log.STATS["writes"] == 1


def test_record_never_raises_and_counts_failures():
    assert answer_log.record(_factory(RecordingSession(fail_commit=True)), **_fields()) is False

    def broken_factory():
        raise RuntimeError("MYSQL_HOST is not set")

    assert answer_log.record(broken_factory, **_fields()) is False
    assert answer_log.STATS["write_failures"] == 2


def test_submit_runs_in_the_background_and_settle_never_raises():
    calls = []

    def boom(**kwargs):
        raise RuntimeError("unexpected")

    async def scenario():
        tasks = [
            answer_log.submit(lambda **kwargs: calls.append(kwargs), value=1),
            answer_log.submit(boom),
        ]
        await answer_log.settle(tasks)

    asyncio.run(scenario())
    assert calls == [{"value": 1}]
    assert answer_log.STATS["submit_failures"] == 1


# --- the /api/ask stream --------------------------------------------------------


class StoredAnswerCache:
    """Misses for the first ``misses`` lookups, then returns a stored answer."""

    def __init__(self, misses=0):
        self.misses = misses
        self.puts = 0

    async def get(self, *args):
        if self.misses:
            self.misses -= 1
            return None
        return {
            "answer": "A cached answer.",
            "chunks": [
                {
                    "n": 1,
                    "chunk_id": 7,
                    "text": "x",
                    "source_path": "a.md",
                    "title": "A",
                    "score": 0.9,
                }
            ],
        }

    async def put(self, *args):
        self.puts += 1


class NoCache(StoredAnswerCache):
    async def get(self, *args):
        return None


class BusyLockRedis(MemoryRedis):
    """Another request already holds the answer lock (a concurrent identical question)."""

    async def set(self, key, value, *, ex=None, nx=False, px=None):
        if nx and key.startswith("lock:"):
            return False
        return await super().set(key, value, ex=ex, nx=nx, px=px)


class Budget:
    async def reserve(self, *, units=4):
        return True


class DenyRate:
    async def allow(self, client_hash):
        return False, 30


@pytest.fixture
def stream(monkeypatch):
    from services.glassbox.api import ask

    state = {"redis": MemoryRedis(), "cache": NoCache(), "rate": AllowAllRate(), "saved": []}
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: state["redis"])
    monkeypatch.setattr(ask, "get_answer_cache", lambda client: state["cache"])
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: Budget())
    monkeypatch.setattr(ask, "get_rate_limiter", lambda client: state["rate"])
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: state["saved"].append(kwargs))

    def post(question="What is Basel's background?"):
        return events(
            TestClient(app).post("/api/ask", json={"question": question, "corpus": "about_me"})
        )

    state["post"] = post
    return state


def test_generated_answer_logs_answer_route_model_and_abstention(stream):
    frames = stream["post"]()
    done = frames[-1][1]
    sent = "".join(data["text"] for name, data in frames if name == "token")
    [saved] = stream["saved"]
    assert saved["answer"] == sent and sent
    assert saved["abstained"] is done["abstained"] is False
    assert saved["route"] == "strict"
    assert saved["llm_model_id"] == "fake-llm-v1"
    assert saved["cache_status"] == "miss" and saved["mode"] == "full"


def test_cache_hit_logs_the_replayed_answer(stream):
    stream["cache"] = StoredAnswerCache()
    stream["post"]()
    [saved] = stream["saved"]
    assert saved["cache_status"] == "answer_hit"
    assert saved["answer"] == "A cached answer." and saved["abstained"] is False
    assert saved["route"] == "strict"


def test_waiting_on_a_concurrent_identical_question_logs_coalesced(stream):
    stream["redis"] = BusyLockRedis()
    # Both routes miss on the first read; the in-flight writer's answer appears next.
    stream["cache"] = StoredAnswerCache(misses=2)
    stream["post"]()
    [saved] = stream["saved"]
    assert saved["cache_status"] == "coalesced"
    assert saved["answer"] == "A cached answer."


def test_no_sources_abstention_is_logged_as_abstained(stream):
    stream["redis"] = MemoryRedis(outcome="empty")
    frames = stream["post"]()
    [saved] = stream["saved"]
    assert saved["abstained"] is frames[-1][1]["abstained"] is True
    assert saved["route"] is None


def test_rate_limited_request_logs_nothing(stream):
    stream["rate"] = DenyRate()
    frames = stream["post"]()
    assert frames[-1][0] == "error" and frames[-1][1]["code"] == "rate_limited"
    assert stream["saved"] == []


def test_kill_switch_request_logs_no_answer(stream):
    stream["redis"].cache["glassbox:kill:disable_llm"] = "1"
    frames = stream["post"]()
    assert frames[-1][1]["mode"] == "retrieval_only"
    [saved] = stream["saved"]
    assert saved["mode"] == "retrieval_only"
    assert saved["answer"] is None and saved["abstained"] is None


def test_done_is_sent_before_the_log_write_finishes(stream, monkeypatch):
    """The write runs in a thread; the stream doesn't wait for it before `done`."""
    from services.glassbox.api import ask

    order = []

    def slow_save(**kwargs):
        import time

        time.sleep(0.2)
        order.append("saved")

    monkeypatch.setattr(ask, "_save_query", slow_save)

    async def scenario():
        request = ask.AskRequest(question="Who?", corpus="about_me")
        async for chunk in ask._stream(request, "01ANSWERLOGORDER0000000000", 0, "client"):
            if chunk.startswith("event: done"):
                order.append("done")

    asyncio.run(scenario())
    assert order == ["done", "saved"]


# --- MySQL integration: exactly one row -------------------------------------------


@pytest.fixture
def mysql(monkeypatch):
    from services.glassbox.db.session import create_db_engine, get_session_factory

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
            columns = {row[0] for row in connection.exec_driver_sql("SHOW COLUMNS FROM queries")}
    except Exception as exc:  # pragma: no cover - depends on local services
        engine.dispose()
        get_session_factory.cache_clear()
        pytest.skip(f"MySQL unavailable: {exc}")
    if "answer" not in columns:
        engine.dispose()
        get_session_factory.cache_clear()
        message = "MySQL queries table not migrated to 0008 (no answer column)"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    yield get_session_factory()
    engine.dispose()
    get_session_factory.cache_clear()


def _rows(sessions, question):
    with sessions() as session:
        return session.scalars(select(Query).where(Query.question == question)).all()


def test_an_answer_writes_exactly_one_row_and_a_rate_limited_request_none(
    stream, mysql, monkeypatch
):
    from services.glassbox.api import ask

    monkeypatch.setattr(ask, "_save_query", ORIGINAL_SAVE_QUERY)
    question = f"Answer log integration {uuid.uuid4().hex}?"
    limited = f"Answer log rate limited {uuid.uuid4().hex}?"
    try:
        frames = stream["post"](question)
        [row] = _rows(mysql, question)
        sent = "".join(data["text"] for name, data in frames if name == "token")
        assert row.answer == sent
        assert row.abstained is False
        assert (row.answer_route, row.llm_model_id, row.prompt_version) == (
            "strict",
            "fake-llm-v1",
            ask._PROMPT_VERSION,
        )
        assert row.cache_status == "miss" and row.chunk_ids == [42]

        stream["rate"] = DenyRate()
        stream["post"](limited)
        assert _rows(mysql, limited) == []
    finally:
        with mysql() as session:
            session.execute(delete(Query).where(Query.question.in_([question, limited])))
            session.commit()


def test_retention_purge_deletes_only_expired_rows(mysql):
    old_id, new_id = "01ANSWERLOGOLD000000000000", "01ANSWERLOGNEW000000000000"
    try:
        with mysql() as session:
            for request_id in (old_id, new_id):
                session.add(
                    Query(
                        request_id=request_id,
                        corpus="about_me",
                        question="retention test",
                        cache_status="miss",
                        mode="full",
                    )
                )
            session.commit()
            session.execute(
                text(
                    "UPDATE queries SET created_at = NOW() - INTERVAL 91 DAY "
                    "WHERE request_id = :request_id"
                ),
                {"request_id": old_id},
            )
            session.commit()
        with mysql() as session:
            assert answer_log.purge_expired(session, days=90) >= 1
        with mysql() as session:
            left = session.scalars(
                select(Query.request_id).where(Query.request_id.in_([old_id, new_id]))
            ).all()
            assert left == [new_id]
    finally:
        with mysql() as session:
            session.execute(delete(Query).where(Query.request_id.in_([old_id, new_id])))
            session.commit()


def test_cache_hit_rate_query_runs(mysql):
    """The documented daily hit-rate query (DESIGN-005 §5.6) is valid SQL on the schema."""
    with mysql() as session:
        rows = session.execute(text(answer_log.CACHE_HIT_RATE_SQL)).all()
    assert all(len(row) == 5 for row in rows)


def test_answer_cap_counts_utf8_bytes_without_splitting_characters():
    answer = "é" * 40000 + "🙂" * 10000  # 80,000 + 40,000 bytes, far over TEXT's 65,535
    row = answer_log.build_row(**_fields(answer=answer))
    encoded = row.answer.encode("utf-8")
    assert len(encoded) <= answer_log.ANSWER_MAX_BYTES < 65535
    assert answer.startswith(row.answer)  # a clean prefix: no half character, no U+FFFD
    assert answer_log.cap_utf8_bytes("a🙂", 3) == "a"
    assert answer_log.cap_utf8_bytes("short", 10) == "short"
    assert answer_log.cap_utf8_bytes(None, 10) is None


def test_failed_write_logs_no_question_or_answer_text(caplog):
    """SQLAlchemy errors quote bound parameters; the log line must carry only the type."""
    from sqlalchemy.exc import OperationalError

    question = "SECRET-QUESTION-TEXT about my plans"
    answer = "SECRET-ANSWER-TEXT for you"

    class LeakySession(RecordingSession):
        def commit(self):
            raise OperationalError(
                "INSERT INTO queries", {"question": question, "answer": answer}, Exception("gone")
            )

    with caplog.at_level("DEBUG", logger=answer_log.LOGGER.name):
        written = answer_log.record(
            _factory(LeakySession()), **_fields(question=question, answer=answer)
        )
    assert written is False
    assert "OperationalError" in caplog.text
    assert "SECRET-QUESTION-TEXT" not in caplog.text and "SECRET-ANSWER-TEXT" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_engine_hides_bound_parameters_in_errors(monkeypatch):
    from services.glassbox.db.session import create_db_engine

    for key, value in {
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_USER": "glassbox",
        "MYSQL_PASSWORD": "unused",  # pragma: allowlist secret (never connects)
        "MYSQL_DATABASE": "glassbox",
    }.items():
        monkeypatch.setenv(key, value)
    engine = create_db_engine()
    try:
        assert engine.hide_parameters is True
    finally:
        engine.dispose()

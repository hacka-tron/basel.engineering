import logging
import threading
import time

import pytest

from services.glassbox.db import wait_for_migrations as wfm

HEAD = frozenset({"0003_query_turn_columns"})
OLD = frozenset({"0002_add_queries_table"})


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def sequence(*results):
    """Return a get_current stub yielding each result in turn (last one repeats)."""
    items = list(results)

    def get_current():
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        return item

    return get_current


def run(get_current, *, timeout=300.0, interval=5.0):
    clock = FakeClock()
    ok = wfm.wait_for_migrations(
        get_current, HEAD, timeout=timeout, interval=interval, clock=clock, sleep=clock.sleep
    )
    return ok, clock


def test_passes_immediately_when_database_is_at_head():
    ok, clock = run(sequence(HEAD))
    assert ok is True
    assert clock.sleeps == []


def test_waits_then_passes_once_migration_lands(caplog):
    caplog.set_level(logging.INFO, logger="glassbox.wait_for_migrations")
    ok, clock = run(sequence(frozenset(), OLD, HEAD))
    assert ok is True
    assert clock.sleeps == [5.0, 5.0]
    assert "database at 0002_add_queries_table" in caplog.text


def test_database_errors_are_retried_not_fatal():
    ok, clock = run(sequence(ConnectionError("mysql not up"), HEAD))
    assert ok is True
    assert clock.sleeps == [5.0]


def test_times_out_with_clear_error(caplog):
    caplog.set_level(logging.INFO, logger="glassbox.wait_for_migrations")
    ok, clock = run(sequence(OLD), timeout=12, interval=5)
    assert ok is False
    # Bounded: never sleeps past the deadline.
    assert clock.now == pytest.approx(12)
    assert clock.sleeps == [5, 5, 2]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "timed out" in errors[0].getMessage()
    assert "0003_query_turn_columns" in errors[0].getMessage()
    assert "job/migrate" in errors[0].getMessage()


def test_revision_ahead_of_image_does_not_pass():
    ok, _ = run(sequence(frozenset({"0004_future"})), timeout=5)
    assert ok is False


def test_head_revisions_come_from_packaged_migrations():
    heads = wfm.get_head_revisions()
    assert len(heads) == 1
    (head,) = heads
    versions = wfm.MIGRATIONS_DIR / "versions"
    assert any(f'revision = "{head}"' in path.read_text() for path in versions.glob("*.py"))


def test_main_exit_codes(monkeypatch):
    monkeypatch.setattr(wfm, "get_head_revisions", lambda: HEAD)

    class FakeEngine:
        def __init__(self, io_timeout):
            self.io_timeout = io_timeout

        def dispose(self) -> None:
            pass

    monkeypatch.setattr(wfm, "create_check_engine", FakeEngine)

    monkeypatch.setattr(wfm, "get_current_revisions", lambda engine: HEAD)
    assert wfm.main(["--timeout", "1"]) == 0

    monkeypatch.setattr(wfm, "get_current_revisions", lambda engine: OLD)
    assert wfm.main(["--timeout", "0.05", "--interval", "0.01"]) == 1


def test_attempts_that_time_out_are_bounded_by_the_deadline():
    """Each attempt gets min(attempt_timeout, remaining); the wait ends exactly at the deadline."""
    clock = FakeClock()
    budgets: list[float] = []

    def stalled_call(fn, budget):
        budgets.append(budget)
        clock.now += budget  # the read hangs for its whole budget...
        raise TimeoutError("revision check did not finish")  # ...then is abandoned

    ok = wfm.wait_for_migrations(
        sequence(HEAD),  # would pass, but never gets to answer
        HEAD,
        timeout=12,
        interval=5,
        attempt_timeout=5,
        clock=clock,
        sleep=clock.sleep,
        call_with_timeout=stalled_call,
    )
    assert ok is False
    assert budgets == [5, 2]
    assert clock.now == pytest.approx(12)


def test_a_hung_database_read_cannot_block_past_the_timeout(caplog):
    """Real clock and threads: get_current blocks far longer than the overall timeout."""
    caplog.set_level(logging.INFO, logger="glassbox.wait_for_migrations")
    release = threading.Event()

    def hung_get_current():
        release.wait(30)
        return HEAD

    start = time.monotonic()
    try:
        ok = wfm.wait_for_migrations(
            hung_get_current, HEAD, timeout=0.5, interval=0.05, attempt_timeout=0.2
        )
    finally:
        release.set()
    elapsed = time.monotonic() - start
    assert ok is False
    assert elapsed < 1.0
    assert "did not finish within" in caplog.text
    assert "timed out after" in caplog.text


def test_call_with_timeout_returns_raises_and_times_out():
    assert wfm._call_with_timeout(lambda: HEAD, 1) == HEAD

    def boom():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        wfm._call_with_timeout(boom, 1)

    release = threading.Event()
    try:
        with pytest.raises(TimeoutError):
            wfm._call_with_timeout(lambda: release.wait(30), 0.05)
    finally:
        release.set()


def test_check_engine_sets_driver_timeouts(monkeypatch):
    for key, value in {
        "MYSQL_USER": "u",
        "MYSQL_PASSWORD": "p",
        "MYSQL_HOST": "h",
        "MYSQL_DATABASE": "d",
    }.items():
        monkeypatch.setenv(key, value)
    captured = {}
    monkeypatch.setattr(wfm, "create_engine", lambda url, **kw: captured.update(kw))
    wfm.create_check_engine(5)
    assert captured["connect_args"] == {
        "connect_timeout": 5,
        "read_timeout": 5,
        "write_timeout": 5,
    }
    assert captured["poolclass"] is wfm.NullPool

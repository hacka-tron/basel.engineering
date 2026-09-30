import logging

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
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(wfm, "create_db_engine", FakeEngine)

    monkeypatch.setattr(wfm, "get_current_revisions", lambda engine: HEAD)
    assert wfm.main(["--timeout", "0"]) == 0

    monkeypatch.setattr(wfm, "get_current_revisions", lambda engine: OLD)
    assert wfm.main(["--timeout", "0"]) == 1

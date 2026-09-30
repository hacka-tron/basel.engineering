"""Block until the database's Alembic revision matches this image's head.

Runs as an initContainer on the api and retrieval-worker Deployments so a new
image never starts serving against a schema older than the one it was built
for. The migrate Job (``alembic upgrade head``) does the actual migrating;
this only waits for it, read-only, and gives up after a bounded timeout so a
failed migration surfaces as a failed pod with a clear log line instead of a
pod stuck in ``Init`` forever.

    python -m services.glassbox.db.wait_for_migrations [--timeout 300] [--interval 5]
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from services.glassbox.db.session import get_database_url

logger = logging.getLogger("glassbox.wait_for_migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_INTERVAL_SECONDS = 5.0
# Bound on one revision read (connect + queries). Enforced twice: by PyMySQL's
# socket timeouts, and by a hard wall-clock cap in _call_with_timeout, so a DB
# that accepts the connection but never answers can't stall the wait.
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 5.0


def get_head_revisions() -> frozenset[str]:
    """Alembic head(s) shipped in this image's migration scripts."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


def create_check_engine(io_timeout: float = DEFAULT_ATTEMPT_TIMEOUT_SECONDS) -> Engine:
    """A dedicated engine for the check: fresh connection per attempt, finite
    connect/read/write timeouts (the app's shared engine has none)."""
    seconds = max(1, int(io_timeout))
    return create_engine(
        get_database_url(),
        poolclass=NullPool,
        connect_args={
            "connect_timeout": seconds,
            "read_timeout": seconds,
            "write_timeout": seconds,
        },
    )


def _call_with_timeout[T](fn: Callable[[], T], timeout: float) -> T:
    """Run ``fn`` in a daemon thread; raise TimeoutError if it takes longer than
    ``timeout``. A hung call is abandoned (the daemon thread can't block exit)."""
    result: dict[str, object] = {}

    def target() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # re-raised in the caller's thread
            result["error"] = exc

    thread = threading.Thread(target=target, name="revision-check", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"revision check did not finish within {timeout:.1f}s")
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return result["value"]  # type: ignore[return-value]


def get_current_revisions(engine: Engine) -> frozenset[str]:
    """Revision(s) recorded in the database's ``alembic_version`` table (read-only)."""
    with engine.connect() as connection:
        return frozenset(MigrationContext.configure(connection).get_current_heads())


def _fmt(revisions: frozenset[str]) -> str:
    return ",".join(sorted(revisions)) or "<none>"


def wait_for_migrations(
    get_current: Callable[[], frozenset[str]],
    expected: frozenset[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    attempt_timeout: float = DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    call_with_timeout: Callable[[Callable[[], frozenset[str]], float], frozenset[str]] = (
        _call_with_timeout
    ),
) -> bool:
    """Poll ``get_current`` until it equals ``expected``; False on timeout.

    The deadline is checked before every attempt, and each attempt is capped
    at ``min(attempt_timeout, time remaining)``, so the whole wait never runs
    past ``timeout`` (plus thread-scheduling slack). Errors and per-attempt
    timeouts reading the revision (MySQL not up yet, stalled under swap, etc.)
    count as "not yet" rather than failing immediately.
    """
    deadline = clock() + timeout
    current: frozenset[str] | None = None
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            logger.error(
                "timed out after %.0fs waiting for database revision %s (last seen: %s); "
                "check the migrate Job: kubectl -n app logs job/migrate",
                timeout,
                _fmt(expected),
                "unreadable" if current is None else _fmt(current),
            )
            return False

        try:
            current = call_with_timeout(get_current, min(attempt_timeout, remaining))
        except Exception as exc:  # any DB error or attempt timeout means "not yet"
            logger.info("could not read database revision yet: %s", exc)
            current = None
        else:
            if current == expected:
                logger.info("database is at head %s; continuing", _fmt(expected))
                return True
            logger.info(
                "waiting for migrations: database at %s, image expects %s",
                _fmt(current),
                _fmt(expected),
            )

        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(interval, remaining))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--attempt-timeout", type=float, default=DEFAULT_ATTEMPT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s [wait-for-migrations] %(message)s",
    )
    logging.getLogger("alembic").setLevel(logging.WARNING)

    expected = get_head_revisions()
    engine = create_check_engine(args.attempt_timeout)
    try:
        ok = wait_for_migrations(
            lambda: get_current_revisions(engine),
            expected,
            timeout=args.timeout,
            interval=args.interval,
            attempt_timeout=args.attempt_timeout,
        )
    finally:
        engine.dispose()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

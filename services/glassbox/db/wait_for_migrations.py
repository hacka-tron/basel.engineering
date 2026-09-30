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
import time
from collections.abc import Callable
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

from services.glassbox.db.session import create_db_engine

logger = logging.getLogger("glassbox.wait_for_migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_INTERVAL_SECONDS = 5.0


def get_head_revisions() -> frozenset[str]:
    """Alembic head(s) shipped in this image's migration scripts."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


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
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll ``get_current`` until it equals ``expected``; False on timeout.

    Errors reading the current revision (MySQL not up yet, network policy
    warming, etc.) count as "not yet" rather than failing immediately.
    """
    deadline = clock() + timeout
    while True:
        try:
            current = get_current()
        except Exception as exc:  # any DB error means "not yet", retry
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
        if remaining <= 0:
            logger.error(
                "timed out after %.0fs waiting for database revision %s (last seen: %s); "
                "check the migrate Job: kubectl -n app logs job/migrate",
                timeout,
                _fmt(expected),
                "unreadable" if current is None else _fmt(current),
            )
            return False
        sleep(min(interval, remaining))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s [wait-for-migrations] %(message)s",
    )
    logging.getLogger("alembic").setLevel(logging.WARNING)

    expected = get_head_revisions()
    engine = create_db_engine()
    try:
        ok = wait_for_migrations(
            lambda: get_current_revisions(engine),
            expected,
            timeout=args.timeout,
            interval=args.interval,
        )
    finally:
        engine.dispose()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

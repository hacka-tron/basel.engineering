"""Answer log: one ``queries`` row per answered question (RAG plan phase 10).

The query log (``queries``, DESIGN-002 §9.3) already held the question, corpus,
retrieved chunk IDs, timings and token counts. Phase 10 adds what an online review
needs: the answer text, the abstention flag, the prompt version, the answer route
(strict or casual), the answer model ID, and a ``coalesced`` cache status, so the
answer-cache hit rate is computable from the table (DESIGN-005 §5.6).

Privacy (owner decision, 2026-10-03, DESIGN-005 §9 item 6):

* No IP address and no client identity: the salted HMAC used for rate limiting
  stays in Redis with its TTL and is never written here.
* Visitor text is masked before it is stored: emails, phone numbers and
  government IDs in the question, the follow-up rewrite and the answer become
  ``[redacted]`` (the same detector as the personal-data guard, ``privacy.py``).
* Retention: rows older than ``GLASSBOX_QUERY_LOG_RETENTION_DAYS`` (default 90)
  are deleted by a small batched delete that runs after a write, at most once an
  hour per API process. No new job, CronJob or Terraform is needed, and the delete
  uses the ``created_at`` index.

Logging never fails or slows an answer: the API schedules the write in a thread
(``submit``) and only waits for it after the ``done`` event has been sent
(``settle``). Every failure is logged and counted in ``STATS``.
"""

import asyncio
import logging
import os
import threading
import time
from collections import Counter
from collections.abc import Callable

from sqlalchemy import text

from services.glassbox.db.models import Query
from services.glassbox.privacy import EMAIL, GOV_ID, PHONE, redact

LOGGER = logging.getLogger(__name__)

# What is masked in visitor text before it is stored.
LOG_MASK_CATEGORIES = frozenset({EMAIL, PHONE, GOV_ID})
QUESTION_MAX_CHARS = 1000  # queries.question and queries.rewritten_query: VARCHAR(1000)
ANSWER_MAX_CHARS = 60000  # queries.answer: TEXT (65,535 bytes); answers are a few hundred
DEFAULT_RETENTION_DAYS = 90
PURGE_INTERVAL_S = 3600.0
PURGE_BATCH = 1000

# Answer-cache hit rate per day (DESIGN-005 §5.6). Only first questions consult the
# answer cache (follow-ups skip it, DESIGN-002 §5.3), so they are the denominator;
# a coalesced request replayed a concurrent writer's answer and counts as a hit.
CACHE_HIT_RATE_SQL = """
SELECT DATE(created_at) AS day,
       COUNT(*) AS first_questions,
       SUM(cache_status = 'answer_hit') AS hits,
       SUM(cache_status = 'coalesced') AS coalesced,
       ROUND(SUM(cache_status IN ('answer_hit', 'coalesced')) / COUNT(*), 3) AS hit_rate
  FROM queries
 WHERE turn_index = 0 AND created_at >= NOW() - INTERVAL 30 DAY
 GROUP BY DATE(created_at)
 ORDER BY day
"""

# In-process counters: writes, write_failures, purged, purge_failures, submit_failures.
STATS: Counter = Counter()
_purge_lock = threading.Lock()
_last_purge: float | None = None


def mask_text(value: str | None, max_chars: int) -> str | None:
    """``value`` with emails, phone numbers and government IDs masked, then capped."""
    if value is None:
        return None
    masked, _ = redact(value, LOG_MASK_CATEGORIES)
    return masked[:max_chars]


def retention_days() -> int:
    """Days a ``queries`` row is kept (``GLASSBOX_QUERY_LOG_RETENTION_DAYS``, at least 1)."""
    raw = os.environ.get("GLASSBOX_QUERY_LOG_RETENTION_DAYS", "")
    try:
        days = int(raw) if raw.strip() else DEFAULT_RETENTION_DAYS
    except ValueError:
        LOGGER.warning("Ignoring GLASSBOX_QUERY_LOG_RETENTION_DAYS=%r", raw)
        days = DEFAULT_RETENTION_DAYS
    return max(1, days)


def purge_expired(session, *, days: int, batch: int = PURGE_BATCH) -> int:
    """Delete up to ``batch`` rows older than ``days``; returns the number deleted.

    Uses the database clock (``created_at`` is a server-side ``CURRENT_TIMESTAMP``)
    and the ``idx_created`` index; the LIMIT keeps one delete short.
    """
    result = session.execute(
        text("DELETE FROM queries WHERE created_at < NOW() - INTERVAL :days DAY LIMIT :batch"),
        {"days": int(days), "batch": int(batch)},
    )
    session.commit()
    return result.rowcount or 0


def _purge_due(now: float) -> bool:
    return _last_purge is None or now - _last_purge >= PURGE_INTERVAL_S


def maybe_purge(session_factory: Callable, *, now: float | None = None) -> int:
    """Run ``purge_expired`` if the last purge in this process was an hour ago or more."""
    global _last_purge
    now = time.monotonic() if now is None else now
    if not _purge_due(now) or not _purge_lock.acquire(blocking=False):
        return 0
    try:
        if not _purge_due(now):
            return 0
        _last_purge = now
        with session_factory()() as session:
            deleted = purge_expired(session, days=retention_days())
        STATS["purged"] += deleted
        if deleted:
            LOGGER.info("Query log retention: deleted %d row(s)", deleted)
        return deleted
    except Exception:
        STATS["purge_failures"] += 1
        LOGGER.warning("Query log retention purge failed", exc_info=True)
        return 0
    finally:
        _purge_lock.release()


def build_row(
    *,
    request_id: str,
    corpus: str,
    question: str,
    chunk_ids: list[int],
    timings: dict[str, int],
    total_ms: int,
    tokens_in: int,
    tokens_out: int,
    turn_index: int,
    rewritten_query: str | None,
    ttft_ms: int | None,
    cache_status: str,
    mode: str,
    answer: str | None,
    abstained: bool | None,
    route: str | None,
    llm_model_id: str | None,
    prompt_version: str,
) -> Query:
    """The ``queries`` row for one request, with visitor text masked.

    The prompt version, route and model ID are set only when the model wrote the
    answer (now, or for the cached copy a hit replays); the no-sources abstention
    and retrieval-only rows leave them empty.
    """
    generated = answer is not None and route is not None
    return Query(
        request_id=request_id,
        corpus=corpus,
        question=mask_text(question, QUESTION_MAX_CHARS),
        cache_status=cache_status,
        mode=mode,
        chunk_ids=chunk_ids,
        stage_timings_ms=timings,
        total_ms=total_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        turn_index=turn_index,
        rewritten_query=mask_text(rewritten_query, QUESTION_MAX_CHARS),
        ttft_ms=ttft_ms,
        answer=mask_text(answer, ANSWER_MAX_CHARS),
        abstained=abstained,
        answer_route=route if generated else None,
        llm_model_id=llm_model_id if generated else None,
        prompt_version=prompt_version if generated else None,
    )


def record(session_factory: Callable, **fields) -> bool:
    """Write one row (``build_row`` fields); never raises. Returns whether it was written.

    ``session_factory`` is ``db.session.get_session_factory`` (called here, so a
    missing database configuration is also a swallowed, counted failure).
    """
    try:
        row = build_row(**fields)
        with session_factory()() as session:
            session.add(row)
            session.commit()
    except Exception:
        # The query log is stats, not part of answering: a failed insert (for
        # example, new columns missing while migrate is still running) is logged and
        # dropped rather than turning an already-sent answer into an error.
        STATS["write_failures"] += 1
        LOGGER.warning(
            "Query log write failed for %s (%d failure(s) since start)",
            fields.get("request_id"),
            STATS["write_failures"],
            exc_info=True,
        )
        return False
    STATS["writes"] += 1
    maybe_purge(session_factory)
    return True


async def _run(function: Callable, kwargs: dict) -> None:
    try:
        await asyncio.to_thread(function, **kwargs)
    except Exception:
        STATS["submit_failures"] += 1
        LOGGER.warning("Query log task failed", exc_info=True)


def submit(function: Callable, /, **kwargs) -> asyncio.Task:
    """Start ``function(**kwargs)`` in a worker thread without waiting for it."""
    return asyncio.get_running_loop().create_task(_run(function, kwargs))


async def settle(tasks: list[asyncio.Task]) -> None:
    """Wait for submitted writes; called once the response's last event has been sent.

    Shielded, so a client that leaves mid-wait doesn't cancel a write in progress
    (the thread finishes either way); never raises.
    """
    for task in tasks:
        try:
            await asyncio.shield(task)
        except BaseException:
            LOGGER.debug("Query log task not awaited to completion", exc_info=True)

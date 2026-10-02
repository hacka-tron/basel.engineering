"""Incrementally ingest the Glassbox corpora into MySQL and Redis."""

import argparse
import asyncio
import logging
import os
import re
import signal
import struct
import sys
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as redis
from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from services.glassbox.cache.answer import drop_legacy_index
from services.glassbox.db.models import Chunk as DbChunk
from services.glassbox.db.models import Document, IngestionRun
from services.glassbox.db.session import create_db_engine
from services.glassbox.ingest.chunkers.code import chunk_code
from services.glassbox.ingest.chunkers.markdown import chunk_markdown
from services.glassbox.ingest.chunkers.terraform import chunk_terraform
from services.glassbox.ingest.chunkers.yaml_doc import chunk_yaml
from services.glassbox.ingest.reconcile import ReconcileReport, reconcile
from services.glassbox.ingest.redis_index import (
    backfill_model_tags,
    ensure_index,
    replace_document_vectors,
)
from services.glassbox.ingest.scanner import (
    scan_file,
    scan_sources,
    strip_front_matter,
)
from services.glassbox.ingest.sweep import (
    CORPORA,
    SWEEP_MODES,
    SweepPlan,
    clear_scope,
    load_scope_documents,
    log_plan,
    max_fraction_from_env,
    plan_sweep,
    run_sweep,
    sweep_mode_from_env,
    sweep_notes,
)
from services.glassbox.privacy import guard_document, guarded_content_hash, quarantine_categories
from services.glassbox.providers.factory import get_embedding_provider

LOGGER = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[3]
_CHUNKERS = {
    ".md": chunk_markdown,
    ".tf": chunk_terraform,
    ".yml": chunk_yaml,
    ".yaml": chunk_yaml,
    ".py": chunk_code,
    ".ts": chunk_code,
    ".tsx": chunk_code,
}
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


INGEST_LOCK_KEY = "ingest:lock"
# Longer than any ingest run so far; if a run outlives it, the lock just expires.
INGEST_LOCK_TTL_MS = 30 * 60 * 1000
# Exit status of ``--reindex`` when another run holds ``ingest:lock`` (EX_TEMPFAIL).
REINDEX_LOCKED_OUT = 75
_RELEASE_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


_LOCKED_OUT_OUTCOME = {
    "ingest": "it exits 0 so the Job doesn't fail; the next run catches up",
    "reindex": f"it exits {REINDEX_LOCKED_OUT}; run the reindex again once the other run ends",
    "clear": f"it exits {REINDEX_LOCKED_OUT}; run --clear again once the other run ends",
}


@asynccontextmanager
async def ingest_lock(redis_client, mode: str = "ingest"):
    """Hold ``ingest:lock`` for one ingest or reindex; yields False if another holds it."""
    token = uuid.uuid4().hex
    acquired = await redis_client.set(INGEST_LOCK_KEY, token, nx=True, px=INGEST_LOCK_TTL_MS)
    if not acquired:
        message = (
            f"!!! ANOTHER INGEST OR REINDEX HOLDS {INGEST_LOCK_KEY}; this {mode} did nothing "
            f"({_LOCKED_OUT_OUTCOME[mode]}) !!!"
        )
        LOGGER.error(message)
        print(message)
        print(message, file=sys.stderr)
        yield False
        return
    try:
        yield True
    finally:
        # Token-checked: never delete a lock that expired and was taken by another run.
        await redis_client.eval(_RELEASE_LOCK, 1, INGEST_LOCK_KEY, token)


@dataclass
class RunResult:
    docs_changed: int = 0
    chunks_written: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    sweep: list[SweepPlan] = field(default_factory=list)
    reconcile: list[ReconcileReport] = field(default_factory=list)
    locked_out: bool = False
    # Personal-data guard: per about_me document, the categories it redacted.
    pii_redacted: dict[str, Counter] = field(default_factory=dict)


def chunker_for_path(path: Path):
    return _CHUNKERS.get(path.suffix.lower())


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _title(content: str, path: Path) -> str:
    match = _HEADING.search(content)
    return (match.group(1).strip() if match else path.name)[:512]


async def prepare_index(sessions, redis_client) -> None:
    """Create or migrate ``idx:chunks`` and finish any pending model-tag backfill.

    Also drops the unused v1 answer index ``idx:answers`` (keeping its keys,
    which expire by TTL) if it still exists; a no-op afterwards.
    """
    await drop_legacy_index(redis_client)
    index_changed = await ensure_index(redis_client)
    # Retry the backfill if an earlier run stopped after FT.ALTER but before
    # tagging all existing hashes. Untagged vectors stay invisible meanwhile.
    if index_changed or not await redis_client.get("idx:chunks:model-tags-ready"):
        with sessions() as session:
            rows = session.execute(select(DbChunk.id, DbChunk.embedding_model)).all()
        await backfill_model_tags(redis_client, rows)
        # Old retrieval-cache entries may include mixed-model matches.
        for corpus in CORPORA:
            await redis_client.incr(f"corpus:ver:{corpus}")
        await redis_client.set("idx:chunks:model-tags-ready", "1")


def seen_source_paths(root: Path) -> dict[str, set[str]]:
    """Every candidate path the scanner yields, per corpus (quarantined files count as seen)."""
    seen: dict[str, set[str]] = {corpus: set() for corpus in CORPORA}
    for source in scan_sources(root):
        seen[source.corpus].add(source.source_path)
    return seen


async def ingest(
    root: Path = REPO_ROOT,
    *,
    engine: Engine | None = None,
    redis_client=None,
    sweep: str | None = None,
    sweep_max_fraction: float | None = None,
    force_sweep: bool = False,
) -> RunResult:
    """Run ingestion; document-level quarantine is reported in the result.

    After every file has been walked, the stale sweep runs in ``sweep`` mode
    (``off``, ``report`` or ``apply``; default from ``GLASSBOX_INGEST_SWEEP``,
    which defaults to ``report``: log what would be deleted, delete nothing).
    Last, the Redis reconcile (``ingest/reconcile.py``) makes ``idx:chunks`` match
    MySQL again: it runs even when no file changed, since unchanged files are
    skipped above and would otherwise never get lost Redis keys back.
    """
    sweep = sweep_mode_from_env() if sweep is None else sweep
    if sweep not in SWEEP_MODES:
        raise ValueError(f"unknown sweep mode {sweep!r}")
    if sweep_max_fraction is None:
        sweep_max_fraction = max_fraction_from_env()
    own_engine = engine is None
    own_redis = redis_client is None
    if engine is None:
        engine = create_db_engine()
    if redis_client is None:
        redis_client = redis.from_url(os.environ["REDIS_URL"])
    try:
        async with ingest_lock(redis_client) as acquired:
            if not acquired:
                return RunResult(locked_out=True)
            return await _ingest(root, engine, redis_client, sweep, sweep_max_fraction, force_sweep)
    finally:
        if own_redis:
            await redis_client.aclose()
        if own_engine:
            engine.dispose()


async def _ingest(
    root: Path,
    engine: Engine,
    redis_client,
    sweep: str,
    sweep_max_fraction: float,
    force_sweep: bool,
) -> RunResult:
    sessions = sessionmaker(bind=engine)
    result = RunResult()
    run_id = None
    try:
        with sessions.begin() as session:
            run = IngestionRun(
                started_at=_now(), status="running", docs_changed=0, chunks_written=0
            )
            session.add(run)
            session.flush()
            run_id = run.id
        await prepare_index(sessions, redis_client)
        provider = get_embedding_provider()
        quarantine = quarantine_categories()
        seen: dict[str, set[str]] = {corpus: set() for corpus in CORPORA}
        indexed = load_indexed(sessions)
        for source in scan_sources(root):
            # Quarantined or failed files still exist, so they never count as stale.
            seen[source.corpus].add(source.source_path)
            scanned = scan_file(source)
            if scanned.error:
                result.errors[source.source_path] = scanned.error
                LOGGER.warning("Skipped %s: %s", source.source_path, scanned.error)
                continue
            chunker = chunker_for_path(source.path)
            if chunker is None:
                LOGGER.warning("Skipping unsupported extension: %s", source.source_path)
                continue
            assert scanned.content is not None and scanned.content_hash is not None
            content_hash = scanned.content_hash
            if source.corpus == "about_me":
                # The guard version is part of the hash, so new detection rules
                # re-scan every about_me document once instead of skipping it.
                content_hash = guarded_content_hash(content_hash)
            known = indexed.get((source.corpus, source.source_path))
            if (
                known is not None
                and known.content_hash == content_hash
                and known.models == {provider.model_id}
            ):
                continue

            content = scanned.content
            if source.corpus == "about_me":
                # Personal-data guard (privacy.py): redact before chunking, so no
                # chunk, embedding, title or snippet ever holds the value.
                guarded = guard_document(
                    strip_front_matter(content), source.source_path, quarantine=quarantine
                )
                if guarded.counts:
                    result.pii_redacted[source.source_path] = guarded.counts
                if guarded.quarantined:
                    # Like a secret-scanner hit: skipped, still "seen", so the
                    # last good version keeps serving.
                    result.errors[source.source_path] = guarded.quarantined
                    continue
                content = guarded.text
            try:
                chunks = chunker(content, source.source_path)
                vectors = await provider.embed([chunk.text for chunk in chunks])
                if len(vectors) != len(chunks) or any(len(vector) != 512 for vector in vectors):
                    raise ValueError("embedding provider returned an invalid vector batch")
            except Exception as exc:
                result.errors[source.source_path] = str(exc)
                LOGGER.warning("Skipped %s: %s", source.source_path, exc)
                continue
            await write_document(
                sessions,
                redis_client,
                source,
                content_hash=content_hash,
                title=_title(content, source.path),
                chunks=chunks,
                vectors=vectors,
                model_id=provider.model_id,
                known=known,
            )
            result.docs_changed += 1
            result.chunks_written += len(chunks)
        # Reached only when the scan walked every file without raising.
        result.sweep = await run_sweep(
            engine,
            redis_client,
            seen,
            provider.model_id,
            mode=sweep,
            max_fraction=sweep_max_fraction,
            force=force_sweep,
        )
        # After the sweep, so keys it just removed are not counted, and on every
        # run: no embedding call, only id sets, one hash per key and MySQL vectors.
        result.reconcile = await reconcile(engine, redis_client, provider.model_id)
        with sessions.begin() as session:
            run = session.get(IngestionRun, run_id)
            run.status = "succeeded"
            run.finished_at = _now()
            run.docs_changed = result.docs_changed
            run.chunks_written = result.chunks_written
            run.notes = sweep_notes(sweep, result.sweep)
        return result
    except Exception:
        if run_id is not None:
            _mark_run_failed(sessions, run_id, result, sweep)
        raise


def _mark_run_failed(sessions, run_id: int, result: RunResult, sweep: str) -> None:
    """Record ``status='failed'`` in a fresh session, never masking the original error.

    If the run failed because MySQL went away, this write fails too: it is logged
    and swallowed so the caller re-raises (and the Job log shows) the real cause.
    The row then stays ``running``.
    """
    try:
        with sessions.begin() as session:
            run = session.get(IngestionRun, run_id)
            run.status = "failed"
            run.finished_at = _now()
            run.docs_changed = result.docs_changed
            run.chunks_written = result.chunks_written
            run.notes = sweep_notes(sweep, result.sweep)
    except Exception:
        LOGGER.exception(
            "Could not mark ingestion run %s failed; re-raising the error that ended the run",
            run_id,
        )


@dataclass
class IndexedDocument:
    """What MySQL holds for one document: enough for the incremental skip check."""

    document_id: int
    content_hash: str
    chunk_ids: list[int] = field(default_factory=list)
    models: set[str] = field(default_factory=set)


def load_indexed(sessions) -> dict[tuple[str, str], IndexedDocument]:
    """Every indexed document's hash, chunk ids and embedding models, in one query.

    Keyed by ``(corpus, source_path)``. Loaded once per run instead of one
    ``SELECT`` per scanned file; safe because ``ingest:lock`` keeps any other
    writer out until the run ends. Vectors and texts are not loaded.
    """
    with sessions() as session:
        rows = session.execute(
            select(
                Document.corpus,
                Document.source_path,
                Document.id,
                Document.content_hash,
                DbChunk.id,
                DbChunk.embedding_model,
            ).outerjoin(DbChunk, DbChunk.document_id == Document.id)
        ).all()
    indexed: dict[tuple[str, str], IndexedDocument] = {}
    for corpus, source_path, document_id, content_hash, chunk_id, model in rows:
        document = indexed.setdefault(
            (corpus, source_path), IndexedDocument(document_id, content_hash)
        )
        if chunk_id is not None:
            document.chunk_ids.append(chunk_id)
            document.models.add(model)
    return indexed


async def write_document(
    sessions,
    redis_client,
    source,
    *,
    content_hash: str,
    title: str,
    chunks,
    vectors,
    model_id: str,
    known: IndexedDocument | None,
) -> None:
    """Replace one document's chunks in MySQL and Redis; no Redis I/O inside the transaction.

    Order, like the stale sweep's (``sweep.py``): the worker raises when a KNN
    match has no MySQL row, so a Redis key must never name a deleted row.

    1. Delete the old ``chunk:{id}`` keys (and their ``chunktxt:{id}``) and bump
       ``corpus:ver`` (retrieval-cache entries holding the old ids would
       otherwise outlive the rows).
    2. One short MySQL transaction: upsert the document (new ``content_hash``),
       delete its old chunk rows, bulk-insert the new ones, read their ids back.
    3. Write the new keys and bump ``corpus:ver`` again (entries computed while
       the document had no keys must not be reused).

    A crash or failure after step 1 leaves MySQL rows with no Redis keys, never
    keys without rows. Before the commit the old rows and old hash remain, so the
    next run re-ingests the file; after it the new hash matches, the file is
    skipped, and that run's reconcile (``reconcile.py``) writes the missing keys
    from MySQL. Missing keys are repaired without the orphan-deletion guards.
    The answer cache needs nothing extra: its entries check each source key's
    ``content_sha``, and a missing or rewritten key reads as changed.
    """
    corpus = source.corpus
    if known is not None and known.chunk_ids:
        # With each key goes its chunktxt text cache (redis_index.py drops the
        # new ids' cache too, for ids reused after a MySQL wipe).
        await redis_client.delete(
            *(f"chunk:{chunk_id}" for chunk_id in known.chunk_ids),
            *(f"chunktxt:{chunk_id}" for chunk_id in known.chunk_ids),
        )
        await redis_client.incr(f"corpus:ver:{corpus}")
    packed = [struct.pack(f"{len(vector)}f", *vector) for vector in vectors]
    with sessions.begin() as session:
        document = session.get(Document, known.document_id) if known is not None else None
        if document is None:
            document = Document(
                corpus=corpus,
                source_path=source.source_path,
                content_hash=content_hash,
                title=title,
            )
            session.add(document)
            session.flush()
        else:
            document.content_hash = content_hash
            document.title = title
            document.updated_at = _now()
        document_id = document.id
        # Normally the ids deleted in step 1; read here so a key step 1 did not
        # know about is removed in step 3 too.
        old_ids = list(
            session.scalars(select(DbChunk.id).where(DbChunk.document_id == document_id))
        )
        if old_ids:
            session.execute(delete(DbChunk).where(DbChunk.document_id == document_id))
        rows = [
            {
                "document_id": document_id,
                "ordinal": ordinal,
                "text": chunk.text,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "token_count": chunk.token_count,
                "embedding": vector,
                "embedding_model": model_id,
            }
            for ordinal, (chunk, vector) in enumerate(zip(chunks, packed, strict=True))
        ]
        if rows:
            # One executemany (PyMySQL batches it into multi-row INSERTs), then one
            # SELECT for the ids, instead of a flush per chunk.
            session.execute(insert(DbChunk), rows)
        new_ids = list(
            session.scalars(
                select(DbChunk.id)
                .where(DbChunk.document_id == document_id)
                .order_by(DbChunk.ordinal)
            )
        )
    new_vectors = [
        (chunk_id, corpus, vector, source.source_path, document_id, chunk.text)
        for chunk_id, chunk, vector in zip(new_ids, chunks, packed, strict=True)
    ]
    await replace_document_vectors(redis_client, old_ids, new_vectors, model_id)
    await redis_client.incr(f"corpus:ver:{corpus}")


async def reindex(
    *, engine: Engine | None = None, redis_client=None
) -> list[ReconcileReport] | None:
    """Rewrite every Redis chunk key of the configured model from MySQL (``--reindex``).

    For after a MySQL restore or a suspect index. Scans no files and calls no
    embedding API (the vectors come from MySQL); idempotent. Orphan keys are
    removed under the zero-row guard (the fraction guard is lifted). Returns None,
    doing nothing, when another ingest or reindex holds ``ingest:lock``.

    SIGTERM (Kubernetes stopping the Ops · Reindex Job at its deadline) cancels
    the run, so the lock is released on the way out instead of blocking the next
    release's ingest for up to its 30-minute TTL; ``asyncio.CancelledError``
    then propagates to the caller.
    """
    model_id = get_embedding_provider().model_id
    own_engine = engine is None
    own_redis = redis_client is None
    engine = engine or create_db_engine()
    if redis_client is None:
        redis_client = redis.from_url(os.environ["REDIS_URL"])
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
        handles_sigterm = True
    except (NotImplementedError, RuntimeError, ValueError):  # not the main thread
        handles_sigterm = False
    try:
        async with ingest_lock(redis_client, "reindex") as acquired:
            if not acquired:
                return None
            await prepare_index(sessionmaker(bind=engine), redis_client)
            return await reconcile(engine, redis_client, model_id, force=True)
    finally:
        if handles_sigterm:
            loop.remove_signal_handler(signal.SIGTERM)
        if own_redis:
            await redis_client.aclose()
        if own_engine:
            engine.dispose()


def dry_run_sweep(
    root: Path = REPO_ROOT,
    *,
    engine: Engine | None = None,
    model_id: str | None = None,
    sweep_max_fraction: float | None = None,
    force_sweep: bool = False,
) -> list[SweepPlan]:
    """Read-only: scan file paths and list what the stale sweep would delete.

    Nothing is embedded or written, and Redis is not touched.
    """
    if sweep_max_fraction is None:
        sweep_max_fraction = max_fraction_from_env()
    model_id = model_id or get_embedding_provider().model_id
    own_engine = engine is None
    engine = engine or create_db_engine()
    try:
        seen = seen_source_paths(root)
        plans = []
        for corpus in CORPORA:
            plan = plan_sweep(
                corpus,
                model_id,
                load_scope_documents(engine, corpus, model_id),
                seen[corpus],
                max_fraction=sweep_max_fraction,
                force=force_sweep,
            )
            log_plan(plan, "dry-run")
            plans.append(plan)
        return plans
    finally:
        if own_engine:
            engine.dispose()


async def clear(
    corpus: str,
    *,
    model_id: str | None = None,
    dry_run: bool = False,
    engine: Engine | None = None,
    redis_client=None,
    documents=None,
    orphan_chunk_ids=None,
):
    """Wipe one corpus/model scope's documents, chunks and Redis keys (incl. orphans).

    ``documents`` and ``orphan_chunk_ids`` limit the wipe to an already listed (and
    confirmed) set. Redis is read even on a dry run, to count orphan keys. A real
    wipe holds ``ingest:lock`` (a concurrent ingest would rewrite what it deletes);
    it returns None, deleting nothing, when another ingest or reindex holds it.
    """
    model_id = model_id or get_embedding_provider().model_id
    own_engine = engine is None
    own_redis = redis_client is None
    engine = engine or create_db_engine()
    if redis_client is None:
        redis_client = redis.from_url(os.environ["REDIS_URL"])
    try:
        if dry_run:
            return await clear_scope(
                engine, redis_client, corpus, model_id, dry_run=True, documents=documents,
                orphan_chunk_ids=orphan_chunk_ids,
            )  # fmt: skip
        async with ingest_lock(redis_client, "clear") as acquired:
            if not acquired:
                return None
            return await clear_scope(
                engine,
                redis_client,
                corpus,
                model_id,
                dry_run=False,
                documents=documents,
                orphan_chunk_ids=orphan_chunk_ids,
            )
    finally:
        if own_redis:
            await redis_client.aclose()
        if own_engine:
            engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m services.glassbox.ingest.run",
        description=(
            "Ingest the corpora, then run the stale sweep in the mode set by "
            "GLASSBOX_INGEST_SWEEP (off, report or apply; default report)."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--sweep",
        action="store_true",
        help="delete stale documents after ingesting (same as GLASSBOX_INGEST_SWEEP=apply)",
    )
    mode.add_argument("--no-sweep", action="store_true", help="skip the stale sweep entirely")
    mode.add_argument(
        "--clear",
        action="store_true",
        help="wipe one corpus/model scope instead of ingesting (needs --corpus)",
    )
    mode.add_argument(
        "--reindex",
        action="store_true",
        help="rewrite every Redis chunk key from MySQL (no file scan, no embedding calls); "
        "use after a MySQL restore or Redis data loss",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read-only: print what the sweep (or --clear) would delete; ingests nothing",
    )
    parser.add_argument(
        "--force-sweep",
        action="store_true",
        help="allow a sweep above the stale-fraction limit (never one with zero scanned files)",
    )
    parser.add_argument("--corpus", choices=CORPORA, help="corpus for --clear")
    parser.add_argument(
        "--model", help="embedding model id for --clear/--dry-run (default: the configured one)"
    )
    parser.add_argument("--yes", action="store_true", help="skip the --clear confirmation prompt")
    return parser


def _confirm_clear(corpus: str, model_id: str, count: int, orphans: int = 0) -> bool:
    if not sys.stdin.isatty():
        print("--clear needs --yes when not run interactively", file=sys.stderr)
        return False
    answer = input(
        f"Delete {count} {corpus} documents and their {model_id} chunks and Redis keys "
        f"(plus {orphans} orphan Redis chunk keys)? "
        f"Type the corpus name to confirm: "
    )
    return answer.strip() == corpus


def _print_reconcile(reports: list[ReconcileReport]) -> None:
    for report in reports:
        print(
            f"reconcile {report.corpus}: repaired={len(report.repaired)} "
            f"rewritten={len(report.rewritten)} removed={len(report.removed)} "
            f"skipped={len(report.skipped)} (mysql_chunks={report.mysql_chunks} "
            f"redis_keys={report.redis_keys})"
        )
        if report.refused:
            banner = (
                f"!!! REDIS RECONCILE REFUSED for {report.corpus} ({report.model_id}): "
                f"{report.refused}. No Redis key was deleted for this corpus. !!!"
            )
            print(banner)
            print(banner, file=sys.stderr)


def _print_refusals(plans: list[SweepPlan]) -> None:
    """Make a refused sweep impossible to miss in the Job log (stdout and stderr).

    The refusal is also stored in ``ingestion_runs.notes``, but a refusal deliberately
    doesn't fail the run (a retry would refuse again and skip the warm-up).
    """
    for plan in plans:
        if plan.refused:
            banner = (
                f"!!! STALE SWEEP REFUSED for {plan.corpus} ({plan.model_id}): {plan.refused}. "
                "Nothing was deleted for this corpus. !!!"
            )
            print(banner)
            print(banner, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("services.glassbox.ingest").setLevel(logging.INFO)
    args = _parser().parse_args(argv)
    conflicts = [
        (args.clear and args.force_sweep, "--force-sweep has no effect with --clear"),
        (args.dry_run and args.sweep, "--sweep can't be combined with --dry-run"),
        (args.dry_run and args.no_sweep, "--no-sweep can't be combined with --dry-run"),
        (args.reindex and args.dry_run, "--reindex can't be combined with --dry-run"),
        (args.reindex and args.force_sweep, "--force-sweep has no effect with --reindex"),
        (args.yes and not args.clear, "--yes is only used with --clear"),
        (
            args.model and not (args.clear or args.dry_run),
            "--model is only used with --clear or --dry-run (ingest uses the configured model)",
        ),
    ]
    for conflict, message in conflicts:
        if conflict:
            print(message, file=sys.stderr)
            return 2
    if args.clear:
        if not args.corpus:
            print("--clear requires --corpus", file=sys.stderr)
            return 2
        try:
            model_id = args.model or get_embedding_provider().model_id
            planned = asyncio.run(clear(args.corpus, model_id=model_id, dry_run=True))
            orphans = len(planned.orphan_chunk_ids)
        except Exception:
            LOGGER.exception("Clear dry run failed")
            return 1
        if args.dry_run:
            print(
                f"dry run: would clear {len(planned.documents)} {args.corpus} documents "
                f"and {orphans} orphan Redis chunk keys ({model_id})"
            )
            return 0
        if not planned.documents and not orphans:
            print(f"nothing to clear for {args.corpus} ({model_id})")
            return 0
        if not args.yes and not _confirm_clear(
            args.corpus, model_id, len(planned.documents), orphans
        ):
            print("clear cancelled", file=sys.stderr)
            return 2
        try:
            cleared = asyncio.run(
                clear(
                    args.corpus,
                    model_id=model_id,
                    documents=planned.documents,
                    orphan_chunk_ids=planned.orphan_chunk_ids,
                )
            )
        except Exception:
            LOGGER.exception("Clear failed")
            print(
                f"CLEAR FAILED part-way for {args.corpus} ({model_id}). Redis keys may already "
                "be gone while MySQL rows remain; the next ingest's reconcile would restore "
                "those keys from MySQL rather than finish the wipe. Re-run --clear to finish, "
                "then ingest.",
                file=sys.stderr,
            )
            return 1
        if cleared is None:
            print("clear did nothing: another ingest or reindex is running", file=sys.stderr)
            return REINDEX_LOCKED_OUT
        print(
            f"cleared {len(cleared.documents)} {args.corpus} documents and "
            f"{len(cleared.orphan_chunk_ids)} orphan Redis chunk keys ({model_id})"
        )
        return 0
    if args.corpus:
        print("--corpus is only used with --clear", file=sys.stderr)
        return 2
    if args.reindex:
        try:
            reports = asyncio.run(reindex())
        except asyncio.CancelledError:
            print("reindex stopped by SIGTERM; ingest:lock released", file=sys.stderr)
            return 143
        except Exception:
            LOGGER.exception("Reindex failed")
            return 1
        if reports is None:
            # Unlike the ingest Job's locked-out run (exit 0, the next release
            # catches up), an operator asked for this reindex and it did nothing:
            # fail, so the Ops · Reindex Job reports it.
            print("reindex did nothing: another ingest or reindex is running", file=sys.stderr)
            return REINDEX_LOCKED_OUT
        _print_reconcile(reports)
        return 0
    if args.dry_run:
        try:
            plans = dry_run_sweep(model_id=args.model, force_sweep=args.force_sweep)
        except Exception:
            LOGGER.exception("Dry run failed")
            return 1
        for plan in plans:
            status = "REFUSED" if plan.refused else "ok"
            print(
                f"dry run {plan.corpus}: would delete {len(plan.stale)} of {plan.known} "
                f"documents; {status}"
            )
        _print_refusals(plans)
        return 0
    sweep = "apply" if args.sweep else "off" if args.no_sweep else None
    try:
        result = asyncio.run(ingest(sweep=sweep, force_sweep=args.force_sweep))
    except Exception:
        LOGGER.exception("Ingestion run failed")
        return 1
    if result.locked_out:
        return 0
    print(
        f"docs_changed={result.docs_changed} chunks_written={result.chunks_written} "
        f"documents_skipped={len(result.errors)}"
    )
    for source_path, reason in result.errors.items():
        print(f"skipped {source_path}: {reason}")
    for source_path, counts in result.pii_redacted.items():
        # Categories and counts only: never the redacted values.
        summary = ", ".join(f"{category}={count}" for category, count in sorted(counts.items()))
        print(f"personal data redacted in {source_path}: {summary}")
    for plan in result.sweep:
        action = "deleted" if plan.deleted else "would delete"
        print(f"sweep {plan.corpus}: {action} {len(plan.stale)} of {plan.known}")
    _print_refusals(result.sweep)
    _print_reconcile(result.reconcile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

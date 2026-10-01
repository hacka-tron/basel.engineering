"""Incrementally ingest the public Glassbox corpora into MySQL and Redis."""

import argparse
import asyncio
import logging
import os
import re
import struct
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from services.glassbox.db.models import Chunk as DbChunk
from services.glassbox.db.models import Document, IngestionRun
from services.glassbox.db.session import create_db_engine
from services.glassbox.ingest.chunkers.code import chunk_code
from services.glassbox.ingest.chunkers.markdown import chunk_markdown
from services.glassbox.ingest.chunkers.terraform import chunk_terraform
from services.glassbox.ingest.chunkers.yaml_doc import chunk_yaml
from services.glassbox.ingest.redis_index import (
    backfill_model_tags,
    ensure_index,
    replace_document_vectors,
)
from services.glassbox.ingest.scanner import scan_file, scan_sources, strip_front_matter
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
)
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


@dataclass
class RunResult:
    docs_changed: int = 0
    chunks_written: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    sweep: list[SweepPlan] = field(default_factory=list)


def chunker_for_path(path: Path):
    return _CHUNKERS.get(path.suffix.lower())


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _title(content: str, path: Path) -> str:
    match = _HEADING.search(content)
    return (match.group(1).strip() if match else path.name)[:512]


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
        index_changed = await ensure_index(redis_client)
        # Retry the backfill if an earlier run stopped after FT.ALTER but before
        # tagging all existing hashes. Untagged vectors stay invisible meanwhile.
        if index_changed or not await redis_client.get("idx:chunks:model-tags-ready"):
            with sessions() as session:
                rows = session.execute(select(DbChunk.id, DbChunk.embedding_model)).all()
            await backfill_model_tags(redis_client, rows)
            # Old retrieval-cache entries may include mixed-model matches.
            for corpus in ("about_me", "about_system"):
                await redis_client.incr(f"corpus:ver:{corpus}")
            await redis_client.set("idx:chunks:model-tags-ready", "1")
        provider = get_embedding_provider()
        seen: dict[str, set[str]] = {corpus: set() for corpus in CORPORA}
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
            with sessions() as session:
                existing = session.scalar(
                    select(Document).where(
                        Document.corpus == source.corpus,
                        Document.source_path == source.source_path,
                    )
                )
                if existing is not None and existing.content_hash == scanned.content_hash:
                    models = set(
                        session.scalars(
                            select(DbChunk.embedding_model)
                            .where(DbChunk.document_id == existing.id)
                            .distinct()
                        )
                    )
                    if models == {provider.model_id}:
                        continue

            content = (
                strip_front_matter(scanned.content)
                if source.corpus == "about_me"
                else scanned.content
            )
            try:
                chunks = chunker(content, source.source_path)
                vectors = await provider.embed([chunk.text for chunk in chunks])
                if len(vectors) != len(chunks) or any(len(vector) != 512 for vector in vectors):
                    raise ValueError("embedding provider returned an invalid vector batch")
            except Exception as exc:
                result.errors[source.source_path] = str(exc)
                LOGGER.warning("Skipped %s: %s", source.source_path, exc)
                continue
            new_vectors = []
            with sessions.begin() as session:
                document = session.scalar(
                    select(Document).where(
                        Document.corpus == source.corpus,
                        Document.source_path == source.source_path,
                    )
                )
                if document is None:
                    document = Document(
                        corpus=source.corpus,
                        source_path=source.source_path,
                        content_hash=scanned.content_hash,
                        title=_title(content, source.path),
                    )
                    session.add(document)
                    session.flush()
                else:
                    document.content_hash = scanned.content_hash
                    document.title = _title(content, source.path)
                    document.updated_at = _now()
                old_ids = list(
                    session.scalars(select(DbChunk.id).where(DbChunk.document_id == document.id))
                )
                if old_ids:
                    session.query(DbChunk).filter(DbChunk.document_id == document.id).delete(
                        synchronize_session=False
                    )
                for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                    packed = struct.pack(f"{len(vector)}f", *vector)
                    row = DbChunk(
                        document_id=document.id,
                        ordinal=ordinal,
                        text=chunk.text,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        token_count=chunk.token_count,
                        embedding=packed,
                        embedding_model=provider.model_id,
                    )
                    session.add(row)
                    session.flush()
                    new_vectors.append(
                        (row.id, source.corpus, packed, source.source_path, document.id)
                    )
                await replace_document_vectors(
                    redis_client, old_ids, new_vectors, provider.model_id
                )
            # The DB transaction has committed; invalidate content-dependent
            # caches for this corpus before another request can reuse them.
            await redis_client.incr(f"corpus:ver:{source.corpus}")
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
        with sessions.begin() as session:
            run = session.get(IngestionRun, run_id)
            run.status = "succeeded"
            run.finished_at = _now()
            run.docs_changed = result.docs_changed
            run.chunks_written = result.chunks_written
        return result
    except Exception:
        if run_id is not None:
            with sessions.begin() as session:
                run = session.get(IngestionRun, run_id)
                run.status = "failed"
                run.finished_at = _now()
                run.docs_changed = result.docs_changed
                run.chunks_written = result.chunks_written
        raise
    finally:
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
):
    """Wipe one corpus/model scope's documents, chunks and Redis keys."""
    model_id = model_id or get_embedding_provider().model_id
    own_engine = engine is None
    own_redis = redis_client is None and not dry_run
    engine = engine or create_db_engine()
    if redis_client is None and not dry_run:
        redis_client = redis.from_url(os.environ["REDIS_URL"])
    try:
        return await clear_scope(engine, redis_client, corpus, model_id, dry_run=dry_run)
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


def _confirm_clear(corpus: str, model_id: str, count: int) -> bool:
    if not sys.stdin.isatty():
        print("--clear needs --yes when not run interactively", file=sys.stderr)
        return False
    answer = input(
        f"Delete {count} {corpus} documents and their {model_id} chunks and Redis keys? "
        f"Type the corpus name to confirm: "
    )
    return answer.strip() == corpus


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("services.glassbox.ingest").setLevel(logging.INFO)
    args = _parser().parse_args(argv)
    if args.clear:
        if not args.corpus:
            print("--clear requires --corpus", file=sys.stderr)
            return 2
        try:
            model_id = args.model or get_embedding_provider().model_id
            planned = asyncio.run(clear(args.corpus, model_id=model_id, dry_run=True))
            if args.dry_run:
                print(f"dry run: would clear {len(planned)} {args.corpus} documents ({model_id})")
                return 0
            if not planned:
                print(f"nothing to clear for {args.corpus} ({model_id})")
                return 0
            if not args.yes and not _confirm_clear(args.corpus, model_id, len(planned)):
                print("clear cancelled", file=sys.stderr)
                return 2
            cleared = asyncio.run(clear(args.corpus, model_id=model_id))
        except Exception:
            LOGGER.exception("Clear failed")
            return 1
        print(f"cleared {len(cleared)} {args.corpus} documents ({model_id})")
        return 0
    if args.corpus:
        print("--corpus is only used with --clear", file=sys.stderr)
        return 2
    if args.dry_run:
        try:
            plans = dry_run_sweep(model_id=args.model, force_sweep=args.force_sweep)
        except Exception:
            LOGGER.exception("Dry run failed")
            return 1
        for plan in plans:
            status = f"REFUSED ({plan.refused})" if plan.refused else "ok"
            print(
                f"dry run {plan.corpus}: would delete {len(plan.stale)} of {plan.known} "
                f"documents; {status}"
            )
        return 0
    sweep = "apply" if args.sweep else "off" if args.no_sweep else None
    try:
        result = asyncio.run(ingest(sweep=sweep, force_sweep=args.force_sweep))
    except Exception:
        LOGGER.exception("Ingestion run failed")
        return 1
    print(
        f"docs_changed={result.docs_changed} chunks_written={result.chunks_written} "
        f"documents_skipped={len(result.errors)}"
    )
    for source_path, reason in result.errors.items():
        print(f"skipped {source_path}: {reason}")
    for plan in result.sweep:
        action = "deleted" if plan.deleted else "would delete"
        status = f" REFUSED ({plan.refused})" if plan.refused else ""
        print(f"sweep {plan.corpus}: {action} {len(plan.stale)} of {plan.known}{status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

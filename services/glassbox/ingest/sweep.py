"""Remove indexed documents whose source files are gone, and clear one corpus/model scope.

Two entry points share the same delete path:

* ``plan_sweep`` decides, per corpus and embedding model, which indexed documents
  were not seen by a completed scan. It fails closed: a scan that found no files
  for a corpus, or one that would remove more than ``max_fraction`` of the
  documents already indexed for that scope, is refused unless ``force`` is set
  (the zero-file guard can't be overridden; use ``--clear`` for that). So is one
  where a source directory that has indexed documents (``infra``, ``k8s``,
  ``services``, ``docs``, ``corpus/about-me``) produced zero scanned files: each
  directory alone is under the fraction limit, so an image that stopped copying,
  say, ``docs/`` would otherwise lose every docs document quietly. ``force``
  overrides the directory guard (for a deliberate removal), never the zero-file one.
  The private About Basel checkout (``private/...`` paths) is stricter: a release
  built without it (no ``ABOUT_ME_DEPLOY_KEY``, or a failed checkout) scans zero
  private files, and that is never a reason to delete them, so ``force`` does not
  override the directory guard for it either (use ``--clear`` for a wipe).
* ``clear_scope`` targets every document in one corpus and model scope.

Delete order (ingest's per-document write, ``run.write_document``, uses the same one):
Redis keys first, then the corpus-version bump, then the MySQL rows in their own
short transaction. No MySQL transaction is open during Redis I/O. The worker
raises if a KNN match has no MySQL row, so the opposite order could leave Redis
keys pointing at deleted rows if the second step failed. With this order a
failure after the Redis step leaves MySQL rows without vectors. The next
applied sweep removes them because the file is still missing (it runs before
that run's Redis reconcile, ``reconcile.py``). If that run only reports, the
reconcile writes their keys back from MySQL, so the rows stay retrievable and
consistent until a sweep is applied.

``--clear`` has no such self-repair: its files still exist, so after a failure
between the Redis and MySQL steps the next ingest's reconcile restores the keys
from the remaining rows instead of finishing the wipe. Re-run ``--clear`` to
finish, then ingest.
"""

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from services.glassbox.db.models import Chunk as DbChunk
from services.glassbox.db.models import Document
from services.glassbox.ingest.scanner import PRIVATE_SOURCE_PREFIX

LOGGER = logging.getLogger(__name__)

CORPORA = ("about_me", "about_system")
SWEEP_MODES = ("off", "report", "apply")
DEFAULT_SWEEP_MODE = "report"
DEFAULT_MAX_STALE_FRACTION = 0.30
# Removing this many documents is always allowed, so renaming one or two files in
# the five-document About Basel corpus doesn't trip the fraction guard.
ALWAYS_ALLOWED_STALE = 2


@dataclass(frozen=True)
class ScopedDocument:
    """One indexed document in a corpus/model scope and its chunk ids for that model."""

    document_id: int
    source_path: str
    chunk_ids: tuple[int, ...]


@dataclass
class SweepPlan:
    corpus: str
    model_id: str
    known: int
    seen_files: int
    stale: list[ScopedDocument] = field(default_factory=list)
    refused: str | None = None
    deleted: bool = False


def sweep_mode_from_env() -> str:
    mode = os.environ.get("GLASSBOX_INGEST_SWEEP", DEFAULT_SWEEP_MODE).strip().lower()
    if mode not in SWEEP_MODES:
        raise ValueError(f"GLASSBOX_INGEST_SWEEP must be one of {SWEEP_MODES}, got {mode!r}")
    return mode


def max_fraction_from_env() -> float:
    raw = os.environ.get("GLASSBOX_INGEST_SWEEP_MAX_FRACTION")
    value = DEFAULT_MAX_STALE_FRACTION if raw in (None, "") else float(raw)
    if not 0 <= value <= 1:
        raise ValueError("GLASSBOX_INGEST_SWEEP_MAX_FRACTION must be between 0 and 1")
    return value


PRIVATE_ROOT = PRIVATE_SOURCE_PREFIX.rstrip("/")


def source_root(source_path: str) -> str:
    """The scanned directory a path came from: ``corpus/about-me``, ``private`` or a top dir."""
    if source_path.startswith("corpus/about-me/"):
        return "corpus/about-me"
    return source_path.split("/", 1)[0]


def plan_sweep(
    corpus: str,
    model_id: str,
    known: Iterable[ScopedDocument],
    seen_paths: set[str],
    *,
    max_fraction: float = DEFAULT_MAX_STALE_FRACTION,
    force: bool = False,
) -> SweepPlan:
    """Pure decision logic: which documents are stale, and is deleting them safe."""
    known = list(known)
    plan = SweepPlan(corpus, model_id, known=len(known), seen_files=len(seen_paths))
    plan.stale = sorted(
        (document for document in known if document.source_path not in seen_paths),
        key=lambda document: document.source_path,
    )
    if not plan.stale:
        return plan
    if not seen_paths:
        plan.refused = (
            f"scan found zero files for {corpus}; refusing to delete all {len(known)} "
            "indexed documents (use --clear for a deliberate wipe)"
        )
        return plan
    missing_roots = sorted(
        {source_root(doc.source_path) for doc in plan.stale}
        - {source_root(path) for path in seen_paths}
    )
    if PRIVATE_ROOT in missing_roots:
        plan.refused = (
            f"the private About Basel checkout produced zero scanned files but has indexed "
            f"{corpus} documents (release built without ABOUT_ME_DEPLOY_KEY?); refusing to "
            "sweep, and --force-sweep does not override this (use --clear for a wipe)"
        )
        return plan
    if missing_roots and not force:
        plan.refused = (
            f"source director{'ies' if len(missing_roots) > 1 else 'y'} "
            f"{', '.join(missing_roots)} produced zero scanned files but "
            f"{'have' if len(missing_roots) > 1 else 'has'} indexed {corpus} documents; "
            "refusing to sweep (is it missing from the image? override with --force-sweep)"
        )
        return plan
    fraction = len(plan.stale) / len(known)
    if len(plan.stale) > ALWAYS_ALLOWED_STALE and fraction > max_fraction and not force:
        plan.refused = (
            f"{len(plan.stale)} of {len(known)} indexed {corpus} documents "
            f"({fraction:.0%}) are missing from the scan, above the {max_fraction:.0%} "
            "limit; refusing to sweep (override with --force-sweep)"
        )
    return plan


def load_scope_documents(engine: Engine, corpus: str, model_id: str) -> list[ScopedDocument]:
    """Documents in ``corpus`` that have chunks for ``model_id`` or no chunks at all.

    Documents whose chunks all belong to another embedding model are out of scope,
    so a sweep or clear for one model never touches another model's index.
    """
    with sessionmaker(bind=engine)() as session:
        documents = session.execute(
            select(Document.id, Document.source_path).where(Document.corpus == corpus)
        ).all()
        chunks = session.execute(
            select(DbChunk.id, DbChunk.document_id, DbChunk.embedding_model)
            .join(Document, DbChunk.document_id == Document.id)
            .where(Document.corpus == corpus)
        ).all()
    return scope_documents(documents, chunks, model_id)


def scope_documents(
    documents: Iterable[tuple[int, str]],
    chunks: Iterable[tuple[int, int, str]],
    model_id: str,
) -> list[ScopedDocument]:
    """Pure scoping: ``documents`` are (id, source_path), ``chunks`` (id, document_id, model)."""
    by_document: dict[int, list[tuple[int, str]]] = {}
    for chunk_id, document_id, embedding_model in chunks:
        by_document.setdefault(document_id, []).append((chunk_id, embedding_model))
    scoped = []
    for document_id, source_path in documents:
        rows = by_document.get(document_id, [])
        matching = tuple(sorted(chunk_id for chunk_id, model in rows if model == model_id))
        if matching or not rows:
            scoped.append(ScopedDocument(document_id, source_path, matching))
    return scoped


async def delete_documents(
    engine: Engine, redis_client, corpus: str, model_id: str, documents: list[ScopedDocument]
) -> None:
    """Delete these documents' chunks for ``model_id`` from Redis, then MySQL.

    A document row is removed only when it has no chunks left for any model.
    """
    if not documents:
        return
    chunk_ids = [chunk_id for document in documents for chunk_id in document.chunk_ids]
    for start in range(0, len(chunk_ids), 500):
        batch = chunk_ids[start : start + 500]
        await redis_client.delete(*(f"chunk:{chunk_id}" for chunk_id in batch))
    # Bump before the MySQL delete: a retrieval-cache entry from the old version
    # could otherwise name chunk ids whose rows are about to disappear.
    await redis_client.incr(f"corpus:ver:{corpus}")
    document_ids = [document.document_id for document in documents]
    with sessionmaker(bind=engine).begin() as session:
        if chunk_ids:
            session.query(DbChunk).filter(
                DbChunk.id.in_(chunk_ids), DbChunk.embedding_model == model_id
            ).delete(synchronize_session=False)
        remaining = set(
            session.scalars(
                select(DbChunk.document_id).where(DbChunk.document_id.in_(document_ids)).distinct()
            )
        )
        empty = [document_id for document_id in document_ids if document_id not in remaining]
        if empty:
            session.query(Document).filter(
                Document.id.in_(empty), Document.corpus == corpus
            ).delete(synchronize_session=False)


def log_plan(plan: SweepPlan, mode: str) -> None:
    prefix = f"stale sweep [{plan.corpus}, model {plan.model_id}, mode {mode}]"
    if plan.refused:
        LOGGER.error("%s REFUSED: %s", prefix, plan.refused)
    verb = "deleted" if plan.deleted else "would delete"
    for document in plan.stale:
        LOGGER.warning(
            "%s %s %s (%d chunks)", prefix, verb, document.source_path, len(document.chunk_ids)
        )
    LOGGER.info(
        "%s known=%d seen_files=%d stale=%d",
        prefix,
        plan.known,
        plan.seen_files,
        len(plan.stale),
    )


async def run_sweep(
    engine: Engine,
    redis_client,
    seen_by_corpus: dict[str, set[str]],
    model_id: str,
    *,
    mode: str,
    max_fraction: float = DEFAULT_MAX_STALE_FRACTION,
    force: bool = False,
) -> list[SweepPlan]:
    """Plan (and in ``apply`` mode, execute) the stale sweep for every corpus.

    Call only after a scan that walked every source without raising.
    """
    if mode not in SWEEP_MODES:
        raise ValueError(f"unknown sweep mode {mode!r}")
    if mode == "off":
        return []
    plans = []
    for corpus in CORPORA:
        plan = plan_sweep(
            corpus,
            model_id,
            load_scope_documents(engine, corpus, model_id),
            seen_by_corpus.get(corpus, set()),
            max_fraction=max_fraction,
            force=force,
        )
        if mode == "apply" and plan.stale and not plan.refused:
            await delete_documents(engine, redis_client, corpus, model_id, plan.stale)
            plan.deleted = True
        log_plan(plan, mode)
        plans.append(plan)
    return plans


async def clear_scope(
    engine: Engine,
    redis_client,
    corpus: str,
    model_id: str,
    *,
    dry_run: bool,
    documents: list[ScopedDocument] | None = None,
) -> list[ScopedDocument]:
    """Remove every document in one corpus/model scope (or list them on a dry run).

    Pass ``documents`` (from an earlier dry run) to delete exactly the list that was
    confirmed, rather than re-reading the scope.
    """
    if documents is None:
        documents = load_scope_documents(engine, corpus, model_id)
    documents = sorted(documents, key=lambda doc: doc.source_path)
    verb = "would clear" if dry_run else "clearing"
    for document in documents:
        LOGGER.warning(
            "clear [%s, model %s] %s %s (%d chunks)",
            corpus,
            model_id,
            verb,
            document.source_path,
            len(document.chunk_ids),
        )
    if not dry_run:
        await delete_documents(engine, redis_client, corpus, model_id, documents)
    return documents

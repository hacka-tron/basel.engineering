"""Make the Redis chunk index match MySQL again, without any embedding call.

MySQL owns durability: every chunk row keeps its text, metadata and the packed
embedding vector. Redis holds a derived ``chunk:{id}`` hash per row (indexed by
``idx:chunks``). Incremental ingest skips a document whose content hash and
embedding model already match MySQL, so before this step nothing rebuilt keys
that Redis lost (a lost ``redis-data`` PVC, a FLUSHALL, an OOM kill before the
last save) or that went stale after a MySQL restore; every answer then abstained.

``reconcile`` runs at the end of every ingest run, including runs where no file
changed (the path that used to leave the index empty). For the configured
embedding model it compares id sets and one hash per key:

* **repair**: a MySQL chunk with no Redis key gets its key written from the row.
* **rewrite**: a key whose corpus, model tag, document id, source path or
  ``content_sha`` (SHA-256 of ``chunks.text``) differs from its row is rewritten.
  Keys written before ``content_sha`` existed are rewritten once, which back-fills
  the field.
* **remove**: a key tagged with this model whose id has no MySQL row (in this
  model) is deleted, along with its ``chunktxt:{id}`` text cache.

So a ``chunk:{id}`` key exists only when MySQL row ``{id}`` exists, and its
``content_sha`` names that row's current text: the invariant the answer cache's
source check relies on. Keys tagged with another embedding model are out of scope.

Guard: when MySQL has zero chunks for a corpus (in this model) but Redis has keys
for it, nothing is deleted and the refusal is logged loudly (an empty or wrongly
restored database must not wipe the index); like the stale sweep's zero-file
guard, ``force`` does not lift it.

When anything was written or removed for a corpus, ``corpus:ver:{corpus}`` is
bumped so retrieval-cache entries computed against the old index are not reused.
``force`` (``--reindex``) rewrites every key in scope from MySQL; it is idempotent.

Assumes one ingest at a time (the ingest Job): ingest writes a document's Redis
keys just before its MySQL transaction commits, so a reconcile running alongside
another ingest could see those keys as orphans for a moment.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from services.glassbox.cache.answer import _model_tag
from services.glassbox.db.models import Chunk as DbChunk
from services.glassbox.db.models import Document
from services.glassbox.ingest.redis_index import chunk_content_sha, chunk_fields
from services.glassbox.ingest.sweep import CORPORA

LOGGER = logging.getLogger(__name__)

BATCH = 500
VECTOR_BYTES = 512 * 4
# The fields compared against MySQL, in HMGET order.
_COMPARED = ("corpus", "model", "document_id", "source_path", "content_sha")


@dataclass(frozen=True)
class ScopeRow:
    """What a MySQL chunk row says its Redis key must contain (text reduced to a hash)."""

    chunk_id: int
    corpus: str
    document_id: int
    source_path: str
    content_sha: str

    def expected(self, model_tag: str) -> tuple[str, ...]:
        return (
            self.corpus,
            model_tag,
            str(self.document_id),
            self.source_path,
            self.content_sha,
        )


@dataclass
class ReconcileReport:
    corpus: str
    model_id: str
    forced: bool = False
    mysql_chunks: int = 0
    redis_keys: int = 0
    repaired: list[int] = field(default_factory=list)
    rewritten: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    refused: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.repaired or self.rewritten or self.removed)


def plan_reconcile(
    model_id: str,
    rows: Iterable[ScopeRow],
    keys: dict[int, tuple[str | None, ...]],
    *,
    force: bool = False,
) -> dict[str, ReconcileReport]:
    """Pure decision logic.

    ``rows`` are this model's MySQL chunks; ``keys`` maps every ``chunk:{id}`` in
    Redis to its ``_COMPARED`` fields (``None`` where a field is missing).
    """
    tag = _model_tag(model_id)
    reports = {corpus: ReconcileReport(corpus, model_id, forced=force) for corpus in CORPORA}

    def report(corpus: str) -> ReconcileReport:
        return reports.setdefault(corpus, ReconcileReport(corpus, model_id, forced=force))

    by_id: dict[int, ScopeRow] = {}
    for row in sorted(rows, key=lambda row: row.chunk_id):
        by_id[row.chunk_id] = row
        current = report(row.corpus)
        current.mysql_chunks += 1
        stored = keys.get(row.chunk_id)
        if stored is None:
            current.repaired.append(row.chunk_id)
        elif force or tuple(stored) != row.expected(tag):
            current.rewritten.append(row.chunk_id)
    for chunk_id, stored in sorted(keys.items()):
        corpus, model = stored[0], stored[1]
        if model != tag:
            continue  # another model's key, or an id rewritten above
        current = report(corpus or "unknown")
        current.redis_keys += 1
        if chunk_id not in by_id:
            current.removed.append(chunk_id)
    for current in reports.values():
        if current.removed and current.mysql_chunks == 0:
            current.refused = (
                f"MySQL has zero {model_id} chunks for {current.corpus} but Redis has "
                f"{current.redis_keys} keys for it; refusing to delete them (is the database "
                "empty or restored from the wrong dump?)"
            )
            current.removed = []
    return reports


def load_scope_rows(engine: Engine, model_id: str) -> list[ScopeRow]:
    """Every MySQL chunk embedded with ``model_id``, its text reduced to ``content_sha``."""
    with sessionmaker(bind=engine)() as session:
        result = session.execute(
            select(
                DbChunk.id,
                Document.corpus,
                DbChunk.document_id,
                Document.source_path,
                DbChunk.text,
            )
            .join(Document, DbChunk.document_id == Document.id)
            .where(DbChunk.embedding_model == model_id)
        )
        return [
            ScopeRow(chunk_id, corpus, document_id, source_path, chunk_content_sha(text))
            for chunk_id, corpus, document_id, source_path, text in result
        ]


def load_write_rows(engine: Engine, model_id: str, chunk_ids: list[int]) -> list[tuple]:
    """Full rows (with the stored vector) for the keys about to be written."""
    with sessionmaker(bind=engine)() as session:
        return list(
            session.execute(
                select(
                    DbChunk.id,
                    Document.corpus,
                    DbChunk.embedding,
                    Document.source_path,
                    DbChunk.document_id,
                    DbChunk.text,
                )
                .join(Document, DbChunk.document_id == Document.id)
                .where(DbChunk.id.in_(chunk_ids), DbChunk.embedding_model == model_id)
            ).all()
        )


def _text(value) -> str | None:
    return value.decode() if isinstance(value, bytes) else value


async def load_redis_chunks(client) -> dict[int, tuple[str | None, ...]]:
    """Every ``chunk:{id}`` key (SCAN, bounded batches) with its compared fields."""
    ids: set[int] = set()
    async for key in client.scan_iter(match="chunk:*", count=1000):
        suffix = _text(key).removeprefix("chunk:")
        if suffix.isdigit():
            ids.add(int(suffix))
    ordered = sorted(ids)
    keys: dict[int, tuple[str | None, ...]] = {}
    for start in range(0, len(ordered), BATCH):
        batch = ordered[start : start + BATCH]
        async with client.pipeline(transaction=False) as pipeline:
            for chunk_id in batch:
                pipeline.hmget(f"chunk:{chunk_id}", list(_COMPARED))
            values = await pipeline.execute()
        for chunk_id, fields in zip(batch, values, strict=True):
            if any(value is not None for value in fields):  # gone since the SCAN otherwise
                keys[chunk_id] = tuple(_text(value) for value in fields)
    return keys


async def apply_reconcile(
    engine: Engine, client, model_id: str, reports: dict[str, ReconcileReport]
) -> None:
    removed = [chunk_id for report in reports.values() for chunk_id in report.removed]
    for start in range(0, len(removed), BATCH):
        batch = removed[start : start + BATCH]
        await client.delete(
            *(f"chunk:{chunk_id}" for chunk_id in batch),
            *(f"chunktxt:{chunk_id}" for chunk_id in batch),
        )
    to_write = sorted(
        chunk_id
        for report in reports.values()
        for chunk_id in (*report.repaired, *report.rewritten)
    )
    written: set[int] = set()
    for start in range(0, len(to_write), BATCH):
        batch = to_write[start : start + BATCH]
        rows = load_write_rows(engine, model_id, batch)
        # One MULTI per batch: a rewritten key is replaced atomically (DEL then
        # HSET drops any stray field), so readers never see it missing.
        async with client.pipeline(transaction=True) as pipeline:
            for chunk_id, corpus, vector, source_path, document_id, text in rows:
                if vector is None or len(vector) != VECTOR_BYTES:
                    LOGGER.error("chunk %s has an invalid stored vector; not indexed", chunk_id)
                    continue
                key = f"chunk:{chunk_id}"
                pipeline.delete(key, f"chunktxt:{chunk_id}")
                pipeline.hset(
                    key,
                    mapping=chunk_fields(corpus, model_id, vector, source_path, document_id, text),
                )
                written.add(chunk_id)
            await pipeline.execute()
    for report in reports.values():
        report.skipped = [
            chunk_id
            for chunk_id in (*report.repaired, *report.rewritten)
            if chunk_id not in written
        ]
        report.repaired = [chunk_id for chunk_id in report.repaired if chunk_id in written]
        report.rewritten = [chunk_id for chunk_id in report.rewritten if chunk_id in written]
        if report.changed:
            await client.incr(f"corpus:ver:{report.corpus}")


def log_report(report: ReconcileReport) -> None:
    prefix = f"redis reconcile [{report.corpus}, model {report.model_id}]"
    if report.refused:
        LOGGER.error("%s REFUSED: %s", prefix, report.refused)
    summary = "%s mysql_chunks=%d redis_keys=%d repaired=%d rewritten=%d removed=%d skipped=%d%s"
    level = logging.WARNING if report.changed or report.skipped else logging.INFO
    LOGGER.log(
        level,
        summary,
        prefix,
        report.mysql_chunks,
        report.redis_keys,
        len(report.repaired),
        len(report.rewritten),
        len(report.removed),
        len(report.skipped),
        " (forced reindex)" if report.forced else "",
    )


async def reconcile(
    engine: Engine, client, model_id: str, *, force: bool = False
) -> list[ReconcileReport]:
    """Plan and apply one reconcile for ``model_id``; see the module docstring."""
    rows = load_scope_rows(engine, model_id)
    keys = await load_redis_chunks(client)
    reports = plan_reconcile(model_id, rows, keys, force=force)
    await apply_reconcile(engine, client, model_id, reports)
    for report in reports.values():
        log_report(report)
    return list(reports.values())

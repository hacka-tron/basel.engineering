"""Incrementally ingest the public Glassbox corpora into MySQL and Redis."""

import asyncio
import logging
import os
import re
import struct
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
from services.glassbox.ingest.redis_index import ensure_index, replace_document_vectors
from services.glassbox.ingest.scanner import scan_file, scan_sources, strip_front_matter
from services.glassbox.providers.fake import FakeEmbeddingProvider

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


def chunker_for_path(path: Path):
    return _CHUNKERS.get(path.suffix.lower())


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _title(content: str, path: Path) -> str:
    match = _HEADING.search(content)
    return (match.group(1).strip() if match else path.name)[:512]


async def ingest(
    root: Path = REPO_ROOT, *, engine: Engine | None = None, redis_client=None
) -> RunResult:
    """Run ingestion; document-level quarantine is reported in the result."""
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
        await ensure_index(redis_client)
        provider = FakeEmbeddingProvider()
        for source in scan_sources(root):
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
                        embedding_model="fake-v1",
                    )
                    session.add(row)
                    session.flush()
                    new_vectors.append(
                        (row.id, source.corpus, packed, source.source_path, document.id)
                    )
                await replace_document_vectors(redis_client, old_ids, new_vectors)
            result.docs_changed += 1
            result.chunks_written += len(chunks)
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


def main() -> int:
    try:
        result = asyncio.run(ingest())
    except Exception:
        LOGGER.exception("Ingestion run failed")
        return 1
    print(
        f"docs_changed={result.docs_changed} chunks_written={result.chunks_written} "
        f"documents_skipped={len(result.errors)}"
    )
    for source_path, reason in result.errors.items():
        print(f"skipped {source_path}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

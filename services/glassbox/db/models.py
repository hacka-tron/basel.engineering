from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BLOB, CHAR, ENUM, JSON, MEDIUMTEXT, TIMESTAMP, TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from services.glassbox.corpora import CORPORA


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("corpus", "source_path", name="uq_doc"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    corpus: Mapped[str] = mapped_column(ENUM(*CORPORA), nullable=False)
    source_path: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(CHAR(40))
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_chunk"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[bytes] = mapped_column(BLOB, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    commit_sha: Mapped[str | None] = mapped_column(CHAR(40))
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    docs_changed: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    chunks_written: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    status: Mapped[str] = mapped_column(ENUM("running", "succeeded", "failed"), nullable=False)
    # Run details that have no column of their own: the stale sweep's mode, planned and
    # deleted counts and any refusal reason (see ingest/sweep.py ``sweep_notes``).
    notes: Mapped[dict | None] = mapped_column(JSON)


class Query(Base):
    __tablename__ = "queries"
    __table_args__ = (Index("idx_created", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(CHAR(26), nullable=False)
    corpus: Mapped[str] = mapped_column(ENUM(*CORPORA), nullable=False)
    question: Mapped[str] = mapped_column(String(1000), nullable=False)
    cache_status: Mapped[str] = mapped_column(ENUM("answer_hit", "miss"), nullable=False)
    mode: Mapped[str] = mapped_column(ENUM("full", "retrieval_only", "stopped"), nullable=False)
    chunk_ids: Mapped[list[int] | None] = mapped_column(JSON)
    stage_timings_ms: Mapped[dict[str, int] | None] = mapped_column(JSON)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    # Provider-reported when available (completed Bedrock answers), otherwise a
    # whitespace-word estimate (fake provider, stopped answers); DESIGN-002 §6.6.
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, server_default=text("CURRENT_TIMESTAMP")
    )
    # Added after created_at by migration 0003 (DESIGN-002 §9.3).
    # 0 = first question; a follow-up counts the prior user turns.
    turn_index: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), nullable=False, server_default=text("0")
    )
    # The standalone retrieval query a follow-up was rewritten to, if any.
    rewritten_query: Mapped[str | None] = mapped_column(String(1000))
    # Added by migration 0005 (DESIGN-002 §7.5, §9.3): ms from request receipt to the
    # first streamed answer token; NULL when no answer text was streamed.
    ttft_ms: Mapped[int | None] = mapped_column(Integer)

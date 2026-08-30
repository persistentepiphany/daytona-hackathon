from __future__ import annotations

import time
import uuid

from sqlalchemy import Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


class Base(DeclarativeBase):
    pass


class Paper(Base):
    __tablename__ = "service_papers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    arxiv_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(Text, default="Untitled paper")
    authors_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="ingesting", index=True)
    status_detail: Mapped[str | None] = mapped_column(Text)
    pdf_key: Mapped[str | None] = mapped_column(Text)
    text_key: Mapped[str | None] = mapped_column(Text)
    metadata_key: Mapped[str | None] = mapped_column(Text)
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    text_sha256: Mapped[str | None] = mapped_column(String(64))
    chars: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class Upload(Base):
    __tablename__ = "service_uploads"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    filename: Mapped[str] = mapped_column(Text)
    expected_size: Mapped[int] = mapped_column(Integer)
    expected_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("service_papers.id"))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Job(Base):
    __tablename__ = "service_jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("service_papers.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="PREFLIGHT")
    status_detail: Mapped[str | None] = mapped_column(Text)
    seeds: Mapped[str] = mapped_column(String(200), default="17,41,93")
    pipeline_run_id: Mapped[str | None] = mapped_column(String(80))
    terminal_classification: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    artifact_prefix: Mapped[str | None] = mapped_column(Text)
    github_repo_id: Mapped[str | None] = mapped_column(String(64))
    github_repo_url: Mapped[str | None] = mapped_column(Text)
    github_commit_sha: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    started_at: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    ended_at: Mapped[float | None] = mapped_column(Float)


class Event(Base):
    __tablename__ = "service_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("service_jobs.id"), index=True)
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("service_papers.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(24), default="render")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Gate(Base):
    __tablename__ = "service_gates"
    __table_args__ = (UniqueConstraint("job_id", "gate", name="uq_service_job_gate"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("service_jobs.id"), index=True)
    gate: Mapped[str] = mapped_column(String(8))
    approver: Mapped[str] = mapped_column(String(100))
    approved_at: Mapped[float] = mapped_column(Float, default=time.time)


class Artifact(Base):
    __tablename__ = "service_artifacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("service_jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class EphemeralBlob(Base):
    """Shared, TTL-bound object fallback until external S3 is configured."""

    __tablename__ = "service_ephemeral_blobs"
    __table_args__ = (Index("idx_ephemeral_blobs_expires", "expires_at"),)
    object_key: Mapped[str] = mapped_column(String(500), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)

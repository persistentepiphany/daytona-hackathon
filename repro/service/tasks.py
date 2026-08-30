from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path

from sqlalchemy import delete, select

from .arxiv import extract_pdf, fetch_metadata, fetch_pdf
from .code_search import certify_code_availability
from .config import REPO, settings
from .database import init_db, session_scope
from .events import emit
from .github_publish import GitHubPublisher, repo_slug
from .models import Artifact, Gate, Job, Paper, Upload, new_id
from .object_store import store
from .packaging import collect_run_artifacts, github_snapshot


def _paper_metadata(paper: Paper) -> dict:
    if paper.metadata_key:
        return json.loads(store.get_bytes(paper.metadata_key).decode())
    if paper.source_ref:
        return json.loads((REPO / paper.source_ref / "paper.json").read_text())
    return {"title": paper.title, "authors": json.loads(paper.authors_json or "[]"),
            "pdf_sha256": paper.pdf_sha256}


def _record_ingest_failure(paper_id: str, stage: str, exc: Exception) -> None:
    with session_scope() as session:
        paper = session.get(Paper, paper_id)
        if paper:
            paper.status = "failed"
            paper.status_detail = str(exc)[:1000]
            paper.updated_at = time.time()
            emit(session, paper_id=paper_id, kind="paper.failed", stage=stage,
                 payload={"error": str(exc)[:1000]})


def ingest_arxiv(paper_id: str) -> None:
    init_db()
    try:
        with session_scope() as session:
            paper = session.get(Paper, paper_id)
            if not paper or not paper.arxiv_id:
                return
            emit(session, paper_id=paper_id, kind="paper.fetching", stage="INGEST",
                 payload={"arxiv_id": paper.arxiv_id})
            arxiv_id = paper.arxiv_id
        metadata = fetch_metadata(arxiv_id)
        pdf = fetch_pdf(arxiv_id, settings.max_pdf_bytes)
        extracted = extract_pdf(pdf)
        title_tokens = [token.lower() for token in metadata["title"].split() if len(token) > 4][:5]
        haystack = extracted.text[:12000].lower()
        if title_tokens and sum(token in haystack for token in title_tokens) < min(2, len(title_tokens)):
            raise ValueError("PDF text does not match the arXiv metadata title")
        code_absence = certify_code_availability(metadata["title"], metadata["authors"])
        prefix = f"papers/{paper_id}"
        pdf_key, text_key, metadata_key = f"{prefix}/paper.pdf", f"{prefix}/paper-extract.txt", f"{prefix}/paper.json"
        payload = {**metadata, "pdf_sha256": extracted.pdf_sha256,
                   "text_sha256": extracted.text_sha256, "pages": extracted.pages,
                   "code_absence": code_absence}
        store.put_bytes(pdf_key, pdf, "application/pdf")
        store.put_bytes(text_key, extracted.text.encode(), "text/plain; charset=utf-8")
        store.put_bytes(metadata_key, json.dumps(payload, indent=2).encode(), "application/json")
        with session_scope() as session:
            paper = session.get(Paper, paper_id)
            if not paper:
                return
            paper.title = metadata["title"]
            paper.authors_json = json.dumps(metadata["authors"])
            paper.pdf_key, paper.text_key, paper.metadata_key = pdf_key, text_key, metadata_key
            paper.pdf_sha256, paper.text_sha256 = extracted.pdf_sha256, extracted.text_sha256
            paper.chars = len(extracted.text)
            paper.status = "needs_ocr" if extracted.needs_ocr else "ready"
            paper.status_detail = ("Extracted text is too sparse; OCR text is required."
                                   if extracted.needs_ocr else "Metadata, PDF, and text hashes verified.")
            paper.updated_at = time.time()
            emit(session, paper_id=paper_id, kind="paper.extracted", stage="EXTRACT",
                 payload={"pages": extracted.pages, "chars": len(extracted.text),
                          "needs_ocr": extracted.needs_ocr,
                          "pdf_sha256": extracted.pdf_sha256,
                          "text_sha256": extracted.text_sha256})
    except Exception as exc:
        _record_ingest_failure(paper_id, "INGEST", exc)


def complete_upload(upload_id: str) -> None:
    init_db()
    try:
        with session_scope() as session:
            upload = session.get(Upload, upload_id)
            if not upload:
                return
            key, expected_size, expected_sha = upload.object_key, upload.expected_size, upload.expected_sha256
            paper_id = upload.paper_id
            emit(session, paper_id=paper_id, kind="paper.upload_verifying", stage="INGEST",
                 payload={"filename": upload.filename})
        head = store.head(key)
        if head["size"] != expected_size:
            raise ValueError(f"uploaded size {head['size']} does not match declared size {expected_size}")
        data = store.get_bytes(key, settings.max_pdf_bytes)
        extracted = extract_pdf(data)
        if expected_sha and extracted.pdf_sha256.lower() != expected_sha.lower():
            raise ValueError("uploaded PDF sha256 does not match the declared hash")
        prefix = f"papers/{paper_id}"
        text_key, metadata_key = f"{prefix}/paper-extract.txt", f"{prefix}/paper.json"
        metadata = {"title": upload.filename.rsplit(".", 1)[0], "authors": [],
                    "pdf_sha256": extracted.pdf_sha256, "text_sha256": extracted.text_sha256,
                    "pages": extracted.pages, "source": "direct_upload",
                    "code_absence": {"results": [], "outcome": "NOT_FOUND",
                                     "status": "SKIPPED: direct upload has no authoritative metadata"}}
        store.put_bytes(text_key, extracted.text.encode(), "text/plain; charset=utf-8")
        store.put_bytes(metadata_key, json.dumps(metadata, indent=2).encode(), "application/json")
        with session_scope() as session:
            upload = session.get(Upload, upload_id)
            paper = session.get(Paper, paper_id) if paper_id else None
            if not upload or not paper:
                return
            upload.status = "complete"
            paper.title = metadata["title"]
            paper.pdf_key, paper.text_key, paper.metadata_key = key, text_key, metadata_key
            paper.pdf_sha256, paper.text_sha256 = extracted.pdf_sha256, extracted.text_sha256
            paper.chars = len(extracted.text)
            paper.status = "needs_ocr" if extracted.needs_ocr else "ready"
            paper.status_detail = ("Extracted text is too sparse; OCR text is required."
                                   if extracted.needs_ocr else "Upload and extraction verified.")
            paper.updated_at = time.time()
            emit(session, paper_id=paper.id, kind="paper.extracted", stage="EXTRACT",
                 payload={"pages": extracted.pages, "chars": paper.chars,
                          "needs_ocr": extracted.needs_ocr,
                          "pdf_sha256": extracted.pdf_sha256,
                          "text_sha256": extracted.text_sha256})
    except Exception as exc:
        with session_scope() as session:
            upload = session.get(Upload, upload_id)
            if upload:
                upload.status = "failed"
                if upload.paper_id:
                    paper = session.get(Paper, upload.paper_id)
                    if paper:
                        paper.status = "failed"
                        paper.status_detail = str(exc)[:1000]
                emit(session, paper_id=upload.paper_id, kind="paper.failed", stage="EXTRACT",
                     payload={"error": str(exc)[:1000]})


def _materialize_paper(paper: Paper, destination: Path) -> Path:
    if paper.source == "bundled" and paper.source_ref:
        return REPO / paper.source_ref
    destination.mkdir(parents=True, exist_ok=True)
    metadata = _paper_metadata(paper)
    (destination / "paper.json").write_text(json.dumps(metadata, indent=2))
    (destination / "paper-extract.txt").write_bytes(store.get_bytes(paper.text_key))
    certificate = metadata.get("code_absence") or {"results": [], "outcome": "NOT_FOUND"}
    (destination / "code_absence.json").write_text(json.dumps({
        "results": certificate.get("results", []), "certificate": certificate,
    }, indent=2))
    return destination


def _stage_from_log(line: str) -> tuple[str, str]:
    plain = line.split("] ", 1)[-1]
    if plain.startswith("P0") or plain.startswith("planner") or plain.startswith("contract"):
        return "PREFLIGHT", "render"
    if plain.startswith("G1"):
        return "G1", "render"
    if plain.startswith("P1") or "round " in plain:
        return "P1", "daytona"
    if plain.startswith("P2"):
        return "P2", "daytona"
    if plain.startswith("P3"):
        return "P3", "render"
    return "PREFLIGHT", "render"


def _is_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, (TimeoutError, ConnectionError)) or any(token in text for token in (
        "timeout", "temporarily unavailable", "connection reset", "connection refused",
        "rate limit", "too many requests", "503", "502", "429",
    ))


def run_pipeline(job_id: str) -> None:
    init_db()
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job or job.status in {"awaiting_g3", "publishing", "complete"}:
            return
        paper = session.get(Paper, job.paper_id)
        if not paper or paper.status != "ready":
            job.status = "not_attemptable"
            job.terminal_classification = "NOT ATTEMPTABLE"
            job.error = "paper is not ready for execution"
            job.ended_at = time.time()
            emit(session, job_id=job.id, paper_id=job.paper_id, kind="run.done", stage="PREFLIGHT",
                 payload={"classification": job.terminal_classification, "error": job.error})
            return
        job.attempt += 1
        job.status = "running"
        job.started_at = job.started_at or time.time()
        job.updated_at = time.time()
        pipeline_run_id = f"auto-{job.id}-{job.attempt}"
        job.pipeline_run_id = pipeline_run_id
        seeds = [int(value) for value in job.seeds.split(",")]
        emit(session, job_id=job.id, paper_id=paper.id, kind="run.started", stage="PREFLIGHT",
             payload={"attempt": job.attempt, "pipeline_run_id": pipeline_run_id,
                      "control_plane": "Render", "compute_plane": "Daytona"})

    try:
        from scripts.auto_run import run_auto

        with tempfile.TemporaryDirectory(prefix="snapshot-paper-") as temp:
            with session_scope() as session:
                paper = session.get(Paper, job.paper_id)
                paper_dir = _materialize_paper(paper, Path(temp) / "paper")

            def log(line: str) -> None:
                stage, source = _stage_from_log(line)
                with session_scope() as event_session:
                    emit(event_session, job_id=job_id, paper_id=job.paper_id,
                         kind="pipeline.log", stage=stage, source=source,
                         payload={"text": line[:4000]})

            code = run_auto(paper_dir, seeds=seeds, run_id=pipeline_run_id, log=log)
        run_dir = REPO / "runs" / "auto" / pipeline_run_id
        with session_scope() as session:
            job = session.get(Job, job_id)
            emit(session, job_id=job_id, paper_id=job.paper_id, kind="package.started", stage="PACKAGE",
                 payload={"run_id": pipeline_run_id})
        files = collect_run_artifacts(run_dir)
        prefix = f"runs/{job_id}/{pipeline_run_id}"
        with session_scope() as session:
            session.execute(delete(Artifact).where(Artifact.job_id == job_id))
            for name, data in files.items():
                key = f"{prefix}/{name}"
                digest = store.put_bytes(key, data)
                session.add(Artifact(id=new_id("art"), job_id=job_id, kind="run_output",
                                     object_key=key, filename=name, sha256=digest, size=len(data)))
            job = session.get(Job, job_id)
            job.artifact_prefix = prefix
            job.status = "awaiting_g3"
            job.stage = "G3"
            job.ended_at = time.time()
            job.terminal_classification = {
                0: "EXECUTED", 2: "NOT ATTEMPTABLE", 3: "UNDER_CONSTRAINED",
                4: "DECLINED_CODE_FOUND",
            }.get(code, "PIPELINE_FAILED")
            emit(session, job_id=job_id, paper_id=job.paper_id, kind="run.done", stage="G3",
                 payload={"classification": job.terminal_classification, "exit_code": code,
                          "artifacts": len(files), "approval_required": True})
    except Exception as exc:
        retry = False
        with session_scope() as session:
            retry_job = session.get(Job, job_id)
            if retry_job and retry_job.attempt < 3 and _is_transient(exc):
                retry_job.status = "queued"
                retry_job.error = str(exc)[:2000]
                emit(session, job_id=job_id, paper_id=retry_job.paper_id,
                     kind="run.retrying", stage=retry_job.stage,
                     payload={"attempt": retry_job.attempt, "error": str(exc)[:1000]})
                retry = True
        if retry:
            # RQ's Retry policy will invoke the same durable job again.
            raise
        terminal = {"run_id": pipeline_run_id, "classification": "PIPELINE_FAILED",
                    "error": str(exc)[:2000], "recorded_at": time.time()}
        key = f"runs/{job_id}/{pipeline_run_id}/terminal.json"
        data = json.dumps(terminal, indent=2).encode()
        digest = store.put_bytes(key, data, "application/json")
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.status = "awaiting_g3"
            job.stage = "G3"
            job.error = str(exc)[:2000]
            job.terminal_classification = "PIPELINE_FAILED"
            job.ended_at = time.time()
            job.artifact_prefix = f"runs/{job_id}/{pipeline_run_id}"
            session.add(Artifact(id=new_id("art"), job_id=job_id, kind="terminal_record",
                                 object_key=key, filename="terminal.json", sha256=digest, size=len(data)))
            emit(session, job_id=job_id, paper_id=job.paper_id, kind="run.done", stage="G3",
                 payload={"classification": "PIPELINE_FAILED", "error": str(exc)[:1000],
                          "approval_required": True})


def publish_github(job_id: str) -> None:
    init_db()
    with session_scope() as session:
        job = session.get(Job, job_id)
        gate = session.scalar(select(Gate).where(Gate.job_id == job_id, Gate.gate == "G3"))
        if not job or not gate:
            raise RuntimeError("G3 approval is required before GitHub publication")
        if job.status == "complete" and job.github_commit_sha:
            return
        paper = session.get(Paper, job.paper_id)
        artifacts = session.scalars(select(Artifact).where(Artifact.job_id == job_id)).all()
        job.status = "publishing"
        emit(session, job_id=job_id, paper_id=job.paper_id, kind="github.started",
             stage="GITHUB_PUBLISH", payload={"owner": settings.github_owner, "private": True})
        run_id = job.pipeline_run_id or job.id
        title, identifier = paper.title, paper.arxiv_id or paper.id
    try:
        files = {artifact.filename: store.get_bytes(artifact.object_key) for artifact in artifacts}
        snapshot = github_snapshot(files, run_id)
        readme = (f"# Snapshot reproduction: {title}\n\n"
                  f"Run `{run_id}` produced `{job.terminal_classification}`.\n\n"
                  "This private repository contains code, manifests, hashes, evidence indexes, "
                  "and reports. Source PDFs and datasets are intentionally excluded.\n")
        snapshot["README.md"] = readme.encode()
        publisher = GitHubPublisher()
        result = publisher.publish(name=repo_slug(title, identifier),
                                   description=f"Snapshot reproduction evidence for {title}",
                                   files=snapshot, message=f"Snapshot evidence for {run_id}")
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.status = "complete"
            job.github_repo_id = result["repo_id"]
            job.github_repo_url = result["repo_url"]
            job.github_commit_sha = result["commit_sha"]
            emit(session, job_id=job_id, paper_id=job.paper_id, kind="github.published",
                 stage="GITHUB_PUBLISH", payload={**result, "private": True})
    except Exception as exc:
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.status = "publish_failed"
            job.error = str(exc)[:2000]
            emit(session, job_id=job_id, paper_id=job.paper_id, kind="github.failed",
                 stage="GITHUB_PUBLISH", payload={"error": str(exc)[:1000]})
        raise

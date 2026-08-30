"""Stateless HTTP control plane for durable paper-reproduction jobs.

Production work is dispatched to an RQ background worker. Postgres is the source
of truth. Objects use S3 when configured, or a shared TTL-bound Postgres fallback
while staging credentials are unavailable.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from repro.service.arxiv import ArxivInputError, normalize_arxiv_id
from repro.service.config import settings
from repro.service.database import db_session, init_db, session_scope
from repro.service.events import STAGE_DESCRIPTIONS, emit, event_dict
from repro.service.models import Artifact, Event, Gate, Job, Paper, Upload, new_id
from repro.service.object_store import ObjectStoreError, store
from repro.service.queueing import enqueue
from repro.service.repository import paper_dict, seed_bundled_papers


SEEDS_RE = re.compile(r"^\d+(,\d+)*$")
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
PROXY_UPLOAD_LIMIT = 4 * 1024 * 1024

app = FastAPI(title="Snapshot durable reproduction API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin for origin in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if origin],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("API_TOKEN")
    if expected and authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="bad or missing bearer token")


def _bootstrap() -> None:
    init_db()
    with session_scope() as session:
        seed_bundled_papers(session)


_bootstrap()


class ArxivRequest(BaseModel):
    arxiv_id_or_url: str = Field(min_length=5, max_length=200)


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    size: int = Field(gt=0)
    sha256: str | None = None


class RunRequest(BaseModel):
    paper_id: str | None = None
    paper_dir: str | None = None
    seeds: str = "17,41,93"


def _paper_response(paper: Paper) -> dict:
    """Add object-retention state without pretending expired blobs are ready."""
    out = paper_dict(paper)
    out.update(storage_backend=store.backend, storage_shared=store.is_shared,
               storage_ephemeral=store.is_ephemeral,
               storage_retention_hours=store.retention_hours)
    if paper.source == "bundled" or not any((paper.pdf_key, paper.text_key, paper.metadata_key)):
        return out
    if not all((paper.pdf_key, paper.text_key, paper.metadata_key)):
        if paper.status == "ready":
            out.update(status="expired", ready=False,
                       status_detail="Temporary paper objects are incomplete; submit the arXiv ID again to refetch them.")
        return out
    try:
        heads = [store.head(key) for key in (paper.pdf_key, paper.text_key, paper.metadata_key) if key]
        expiries = [head["expires_at"] for head in heads if head.get("expires_at")]
        out["storage_expires_at"] = min(expiries) if expiries else None
    except (OSError, ObjectStoreError):
        out["storage_expires_at"] = None
        if paper.status == "ready":
            out.update(status="expired", ready=False,
                       status_detail="Temporary paper objects expired; submit the arXiv ID again to refetch them.")
    return out


def _paper_objects_available(paper: Paper) -> bool:
    if paper.source == "bundled":
        return True
    return all(store.exists(key) for key in (paper.pdf_key, paper.text_key, paper.metadata_key))


def _job_dict(session: Session, job: Job, detailed: bool = False) -> dict:
    paper = session.get(Paper, job.paper_id)
    out = {
        "job_id": job.id, "paper_id": job.paper_id, "paper_slug": paper.id if paper else None,
        "title": paper.title if paper else "Reproduction run", "status": job.status,
        "stage": job.stage, "stage_description": STAGE_DESCRIPTIONS.get(job.stage, ""),
        "status_detail": job.status_detail, "seeds": job.seeds,
        "run_id": job.pipeline_run_id, "terminal_classification": job.terminal_classification,
        "error": job.error, "created_at": job.created_at, "started_at": job.started_at,
        "updated_at": job.updated_at, "ended_at": job.ended_at,
        "github_repo_url": job.github_repo_url, "github_commit_sha": job.github_commit_sha,
    }
    if not detailed:
        return out
    events = session.scalars(select(Event).where(Event.job_id == job.id).order_by(Event.id.desc()).limit(100)).all()
    events.reverse()
    out["events"] = [event_dict(event) for event in events]
    out["logs"] = [json.loads(event.payload_json).get("text", "") for event in events
                   if event.kind == "pipeline.log"][-50:]
    stage_order = list(STAGE_DESCRIPTIONS)
    stage_states: dict[str, dict] = {
        stage: {"status": "pending", "detail": "", "description": description,
                "source": "daytona" if stage in {"P1", "P2"} else "render"}
        for stage, description in STAGE_DESCRIPTIONS.items()
    }
    stage_states["INGEST"].update(status="done", detail="Paper source acquired and validated.")
    stage_states["EXTRACT"].update(status="done", detail="PDF text and provenance hashes are ready.")
    for event in events:
        if event.stage in stage_states:
            stage_states[event.stage].update(
                status="running", detail=json.loads(event.payload_json).get("text", event.kind),
                source=event.source,
            )
    current_index = stage_order.index(job.stage) if job.stage in stage_order else 0
    for index, stage in enumerate(stage_order):
        if index < current_index and stage_states[stage]["status"] == "running":
            stage_states[stage]["status"] = "done"
        elif index < current_index and stage_states[stage]["status"] == "pending":
            stage_states[stage]["status"] = "skipped" if stage == "P4" else "done"
    if job.stage in stage_states:
        if job.status == "queued":
            stage_states[job.stage]["status"] = "queued"
        elif job.status == "awaiting_g3":
            stage_states["G3"].update(status="waiting", detail="Explicit publication approval required.")
        elif job.status == "publish_failed":
            stage_states[job.stage]["status"] = "failed"
        elif job.status == "complete":
            stage_states[job.stage]["status"] = "done"
    out["stages"] = stage_states
    artifacts = session.scalars(select(Artifact).where(Artifact.job_id == job.id).order_by(Artifact.filename)).all()
    out["artifacts"] = [{"artifact_id": item.id, "name": item.filename, "kind": item.kind,
                         "sha256": item.sha256, "size": item.size,
                         "url": f"/api/artifacts/{item.id}"} for item in artifacts]
    verdicts = next((item for item in artifacts if item.filename == "verdicts.json"), None)
    if verdicts:
        try:
            out["verdicts"] = json.loads(store.get_bytes(verdicts.object_key, 2 * 1024 * 1024))
        except (ValueError, OSError, ObjectStoreError):
            out["verdicts"] = None
    report = next((item for item in artifacts if item.filename == "report.md"), None)
    if report:
        try:
            out["report"] = store.get_bytes(report.object_key, 2 * 1024 * 1024).decode(errors="replace")
        except (OSError, ObjectStoreError):
            out["report"] = None
    if job.github_repo_url:
        out["repo"] = {
            "name": job.github_repo_url.rstrip("/").rsplit("/", 1)[-1], "url": job.github_repo_url,
            "branch": "main", "commit_sha": job.github_commit_sha,
            "files": [{"label": item.filename,
                       "url": f"{job.github_repo_url}/blob/main/runs/{job.pipeline_run_id}/{item.filename}"}
                      for item in artifacts[:20]],
        }
    return out


@app.get("/healthz")
def healthz() -> dict:
    from repro.env import env_key

    expired_objects_removed = store.cleanup_expired()
    with session_scope() as session:
        session.execute(select(Paper.id).limit(1)).all()
    return {
        "ok": True,
        "authenticated": bool(os.environ.get("API_TOKEN")),
        "durable": bool(settings.redis_url and not settings.database_url.startswith("sqlite") and store.is_shared),
        "services": {"database": "postgres" if not settings.database_url.startswith("sqlite") else "sqlite-dev",
                     "queue": "rq" if settings.redis_url else "thread-dev",
                     "objects": store.backend},
        "storage": {"backend": store.backend, "shared": store.is_shared,
                    "ephemeral": store.is_ephemeral, "retention_hours": store.retention_hours,
                    "expired_objects_removed": expired_objects_removed},
        "keys": {"zai": bool(env_key("ZAI_API_KEY", "ZAI_API")),
                 "daytona": bool(env_key("DAYTONA_API_KEY", "DAYTONA_API")),
                 "parallel": bool(env_key("PARALLEL_API_KEY", "PARALLEL_API")),
                 "github_app_user": bool(settings.github_token)},
    }


@app.get("/stages")
def stages() -> dict[str, str]:
    return STAGE_DESCRIPTIONS


@app.get("/papers", dependencies=[Depends(require_token)])
def papers(session: Session = Depends(db_session)) -> list[dict]:
    return [_paper_response(paper) for paper in
            session.scalars(select(Paper).order_by(Paper.created_at.desc())).all()]


@app.get("/papers/{paper_id}", dependencies=[Depends(require_token)])
def get_paper(paper_id: str, session: Session = Depends(db_session)) -> dict:
    paper = session.get(Paper, paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="no such paper")
    return _paper_response(paper)


@app.post("/papers/arxiv", status_code=202, dependencies=[Depends(require_token)])
def create_arxiv(req: ArxivRequest, session: Session = Depends(db_session)) -> dict:
    try:
        arxiv_id = normalize_arxiv_id(req.arxiv_id_or_url)
    except ArxivInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    existing = session.scalar(select(Paper).where(Paper.arxiv_id == arxiv_id))
    if existing:
        if existing.status == "failed" or (existing.status in {"ready", "expired"} and
                                            not _paper_objects_available(existing)):
            existing.status = "ingesting"
            existing.status_detail = "Refetching metadata and PDF from the arXiv API."
            emit(session, paper_id=existing.id, kind="paper.requeued", stage="INGEST",
                 payload={"arxiv_id": arxiv_id, "reason": "retry_or_expired_storage",
                          "storage_backend": store.backend})
            session.commit()
            enqueue("repro.service.tasks.ingest_arxiv", existing.id,
                    job_id=f"retry-ingest-{existing.id}-{int(time.time())}")
        return _paper_response(existing)
    paper = Paper(id=new_id("paper"), source="arxiv", arxiv_id=arxiv_id,
                  source_ref=f"https://arxiv.org/abs/{arxiv_id}", title=f"arXiv {arxiv_id}",
                  status="ingesting", status_detail="Queued for server-side arXiv download.")
    session.add(paper)
    session.flush()
    emit(session, paper_id=paper.id, kind="paper.queued", stage="INGEST", payload={"arxiv_id": arxiv_id})
    session.commit()
    enqueue("repro.service.tasks.ingest_arxiv", paper.id, job_id=f"ingest-{paper.id}")
    return _paper_response(paper)


@app.post("/papers/uploads", status_code=202, dependencies=[Depends(require_token)])
def create_upload(req: UploadRequest, session: Session = Depends(db_session)) -> dict:
    if req.size > settings.max_pdf_bytes:
        raise HTTPException(status_code=413, detail=f"PDF exceeds {settings.max_pdf_bytes} bytes")
    if not req.filename.lower().endswith(".pdf") or "/" in req.filename or "\\" in req.filename:
        raise HTTPException(status_code=400, detail="filename must be a plain .pdf filename")
    if req.sha256 and not SHA256_RE.fullmatch(req.sha256):
        raise HTTPException(status_code=400, detail="sha256 must contain 64 hexadecimal characters")
    if store.backend == "database" and req.size > PROXY_UPLOAD_LIMIT:
        raise HTTPException(
            status_code=503,
            detail=("Temporary storage cannot proxy PDFs over 4 MiB through the hosted UI. "
                    "Enter an arXiv ID/URL for server-side fetching, or configure S3 for direct uploads."),
        )
    upload_id, paper_id = new_id("upload"), new_id("paper")
    key = f"papers/{paper_id}/paper.pdf"
    paper = Paper(id=paper_id, source="upload", source_ref=req.filename,
                  title=req.filename.removesuffix(".pdf"), status="uploading",
                  status_detail="Waiting for upload to temporary shared storage."
                  if store.is_ephemeral else "Waiting for direct object-storage upload.")
    upload = Upload(id=upload_id, object_key=key, filename=req.filename,
                    expected_size=req.size, expected_sha256=req.sha256, paper_id=paper_id)
    session.add_all([paper, upload])
    session.flush()
    upload_url = (store.presign_put(key, "application/pdf") if store.is_remote
                  else f"/api/papers/uploads/{upload_id}/content")
    return {"upload_id": upload_id, "paper_id": paper_id, "upload_url": upload_url,
            "method": "PUT", "headers": {"Content-Type": "application/pdf"}, "expires_in": 900,
            "storage_backend": store.backend, "storage_ephemeral": store.is_ephemeral,
            "storage_retention_hours": store.retention_hours}


@app.put("/papers/uploads/{upload_id}/content", dependencies=[Depends(require_token)])
async def local_upload(upload_id: str, request: Request, session: Session = Depends(db_session)) -> dict:
    if store.is_remote:
        raise HTTPException(status_code=404, detail="direct content endpoint is disabled for S3 storage")
    upload = session.get(Upload, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="no such upload")
    data = await request.body()
    if len(data) > settings.max_pdf_bytes or len(data) != upload.expected_size:
        raise HTTPException(status_code=400, detail="uploaded size does not match declaration")
    store.put_bytes(upload.object_key, data, "application/pdf")
    return {"ok": True, "size": len(data)}


@app.post("/papers/uploads/{upload_id}/complete", status_code=202,
          dependencies=[Depends(require_token)])
def finish_upload(upload_id: str, session: Session = Depends(db_session)) -> dict:
    upload = session.get(Upload, upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="no such upload")
    if upload.status in {"pending", "failed"}:
        upload.status = "verifying"
        session.commit()
        enqueue("repro.service.tasks.complete_upload", upload.id, job_id=f"extract-{upload.id}")
    return {"upload_id": upload.id, "paper_id": upload.paper_id, "status": upload.status}


@app.post("/runs", status_code=202, dependencies=[Depends(require_token)])
def create_run(req: RunRequest, session: Session = Depends(db_session)) -> dict:
    paper_id = req.paper_id
    if not paper_id and req.paper_dir:
        paper_id = f"bundled-{req.paper_dir.rstrip('/').rsplit('/', 1)[-1]}"
    if not paper_id:
        raise HTTPException(status_code=400, detail="paper_id is required")
    paper = session.get(Paper, paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="no such paper")
    if paper.status != "ready":
        raise HTTPException(status_code=409, detail=f"paper is {paper.status}: {paper.status_detail or ''}")
    if not _paper_objects_available(paper):
        paper.status = "expired"
        paper.status_detail = "Temporary paper objects expired; submit the arXiv ID again to refetch them."
        paper.updated_at = time.time()
        session.commit()
        raise HTTPException(status_code=409, detail=paper.status_detail)
    if not SEEDS_RE.fullmatch(req.seeds):
        raise HTTPException(status_code=400, detail="seeds must be comma-separated integers")
    job = Job(id=new_id("job"), paper_id=paper.id, seeds=req.seeds, status="queued", stage="PREFLIGHT",
              status_detail="Queued for the durable background worker.")
    session.add(job)
    session.flush()
    emit(session, job_id=job.id, paper_id=paper.id, kind="run.queued", stage="PREFLIGHT",
         payload={"paper_id": paper.id, "seeds": req.seeds})
    session.commit()
    enqueue("repro.service.tasks.run_pipeline", job.id, job_id=f"pipeline-{job.id}")
    return _job_dict(session, job)


@app.get("/runs", dependencies=[Depends(require_token)])
def list_runs(session: Session = Depends(db_session)) -> list[dict]:
    return [_job_dict(session, job) for job in
            session.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)).all()]


@app.get("/runs/{job_id}", dependencies=[Depends(require_token)])
def get_run(job_id: str, session: Session = Depends(db_session)) -> dict:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    return _job_dict(session, job, detailed=True)


@app.post("/runs/{job_id}/gates/G3/approve", status_code=202,
          dependencies=[Depends(require_token)])
def approve_g3(job_id: str, x_approver: str | None = Header(default=None),
               session: Session = Depends(db_session)) -> dict:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    if job.status not in {"awaiting_g3", "publish_failed", "publishing", "complete"}:
        raise HTTPException(status_code=409, detail="run is not ready for G3 approval")
    gate = session.scalar(select(Gate).where(Gate.job_id == job_id, Gate.gate == "G3"))
    if not gate:
        gate = Gate(job_id=job_id, gate="G3", approver=(x_approver or "api-user")[:100])
        session.add(gate)
        session.flush()
        emit(session, job_id=job_id, paper_id=job.paper_id, kind="gate.changed", stage="G3",
             payload={"gate": "G3", "state": "approved", "approver": gate.approver})
    if job.status not in {"publishing", "complete"}:
        job.status = "publishing"
        session.commit()
        enqueue("repro.service.tasks.publish_github", job.id, job_id=f"github-{job.id}")
    return {"job_id": job.id, "gate": "G3", "approved": True, "status": job.status}


def _sse(job_id: str, after: int) -> Iterator[str]:
    cursor, deadline = after, time.monotonic() + 900
    while time.monotonic() < deadline:
        with session_scope() as session:
            rows = session.scalars(select(Event).where(Event.job_id == job_id, Event.id > cursor)
                                   .order_by(Event.id).limit(500)).all()
        if rows:
            for row in rows:
                cursor = row.id
                yield f"id: {row.id}\ndata: {json.dumps(event_dict(row), separators=(',', ':'))}\n\n"
        else:
            yield ": keepalive\n\n"
            time.sleep(2)


@app.get("/runs/{job_id}/events", dependencies=[Depends(require_token)])
def run_events(job_id: str, after: int = 0,
               last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
               session: Session = Depends(db_session)):
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="no such job")
    cursor = max(after, int(last_event_id)) if last_event_id and last_event_id.isdigit() else after
    return StreamingResponse(_sse(job_id, cursor), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/runs/{job_id}/feed", response_class=HTMLResponse,
         dependencies=[Depends(require_token)])
def run_feed(job_id: str, session: Session = Depends(db_session)) -> str:
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="no such job")
    return """<!doctype html><meta charset=utf-8><title>Snapshot feed</title>
    <pre id=feed>Connecting…</pre><script>
    const out=document.getElementById('feed'); out.textContent='';
    new EventSource(location.pathname.replace(/\/feed$/, '/events')).onmessage=e=>{
      const x=JSON.parse(e.data); out.textContent += `[${x.source}] ${x.stage} ${x.kind} ${JSON.stringify(x.payload)}\\n`;
    };</script>"""


@app.get("/runs/{job_id}/report", dependencies=[Depends(require_token)])
def report(job_id: str, session: Session = Depends(db_session)) -> Response:
    item = session.scalar(select(Artifact).where(Artifact.job_id == job_id, Artifact.filename == "report.md"))
    if not item:
        raise HTTPException(status_code=404, detail="no report for this run")
    try:
        head = store.head(item.object_key)
        return Response(store.get_bytes(item.object_key), media_type="text/markdown",
                        headers={"X-Snapshot-Storage": store.backend,
                                 "X-Snapshot-Expires-At": str(head.get("expires_at") or "")})
    except (OSError, ObjectStoreError) as exc:
        raise HTTPException(status_code=410, detail="temporary report artifact expired") from exc


@app.get("/artifacts/{artifact_id}", dependencies=[Depends(require_token)])
def artifact(artifact_id: str, session: Session = Depends(db_session)):
    item = session.get(Artifact, artifact_id)
    if not item:
        raise HTTPException(status_code=404, detail="no such artifact")
    if store.is_remote:
        return RedirectResponse(store.presign_get(item.object_key), status_code=307)
    try:
        head = store.head(item.object_key)
        return Response(store.get_bytes(item.object_key), media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{item.filename}"',
                                 "X-Snapshot-Storage": store.backend,
                                 "X-Snapshot-Expires-At": str(head.get("expires_at") or "")})
    except (OSError, ObjectStoreError) as exc:
        raise HTTPException(status_code=410, detail="temporary artifact expired") from exc

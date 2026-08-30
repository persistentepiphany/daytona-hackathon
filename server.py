"""HTTP front door for on-demand autonomous runs.

A run takes minutes and the ledger's SQLite connection tolerates exactly one
writer, so requests enqueue work for a single worker thread rather than doing
it inline. Clients poll GET /runs/{job_id}.
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from repro import ingest

REPO = Path(__file__).resolve().parent
RUN_ROOT = REPO / "runs" / "auto"
JOBS_FILE = RUN_ROOT / "api_jobs.json"
PAPERS = REPO / "papers"
SEEDS_RE = re.compile(r"^\d+(,\d+)*$")

app = FastAPI(title="Preregistered reproduction runs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_q: "queue.Queue[str]" = queue.Queue()

# Ingestion is minutes shorter than a run and must not sit behind one in the
# queue, so it gets its own bounded pool rather than the single run worker.
_ingests: dict[str, dict] = {}
_ingest_lock = threading.Lock()
_ingest_slots = threading.Semaphore(2)
INGEST_KEEP = 40


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("API_TOKEN")
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="bad or missing bearer token")


def _save() -> None:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_FILE.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(_jobs, indent=2))
    tmp.replace(JOBS_FILE)


def _load() -> None:
    if not JOBS_FILE.is_file():
        return
    try:
        stored = json.loads(JOBS_FILE.read_text())
    except (OSError, ValueError):
        return
    for job in stored.values():
        # a job still marked running belongs to an instance that is gone
        if job.get("status") in ("queued", "running"):
            job["status"] = "interrupted"
    _jobs.update(stored)


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)
    _save()


def _resolve_paper(paper_dir: str) -> Path:
    candidate = (REPO / paper_dir).resolve()
    if PAPERS.resolve() not in candidate.parents or not candidate.is_dir():
        raise HTTPException(status_code=400, detail=f"unknown paper_dir: {paper_dir}")
    return candidate


def _worker() -> None:
    while True:
        job_id = _q.get()
        job = _jobs[job_id]
        started = time.time()
        _update(job_id, status="running", started_at=started)
        before = {p.name for p in RUN_ROOT.glob("auto-*")} if RUN_ROOT.is_dir() else set()
        log_path = RUN_ROOT / f"{job_id}.log"
        RUN_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("w") as out:
                proc = subprocess.run(
                    [sys.executable, "scripts/auto_run.py", job["paper_dir"],
                     "--seeds", job["seeds"]],
                    cwd=REPO, stdout=out, stderr=subprocess.STDOUT, timeout=3600,
                    # the run happens in its own process, so it opts into the feed
                    # here; GET /runs/{job_id}/feed then tails its ledger
                    env={**os.environ, "REPRO_TELEMETRY": "1"},
                )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            _update(job_id, status="failed", error="run exceeded 60m", ended_at=time.time())
            _q.task_done()
            continue

        after = {p.name for p in RUN_ROOT.glob("auto-*")} - before
        run_id = sorted(after)[-1] if after else None
        fields = {"run_id": run_id, "exit_code": code, "ended_at": time.time(),
                  "duration_s": round(time.time() - started, 1)}
        # auto_run exits 2 when the build loop never reached smoke and 3 when
        # nothing graded; both still write verdicts.json and report.md, so they
        # are reportable outcomes rather than crashes
        fields["status"] = ({0: "succeeded", 2: "build_failed", 3: "no_verdicts"}
                            .get(code, "failed"))
        if run_id and job.get("publish") and code == 0:
            fields["preview_url"] = _publish(log_path)
        _update(job_id, **fields)
        _q.task_done()


def _publish(log_path: Path) -> str | None:
    try:
        with log_path.open("a") as out:
            subprocess.run([sys.executable, "scripts/publish_auto.py"], cwd=REPO,
                           stdout=out, stderr=subprocess.STDOUT, timeout=1800, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    tail = log_path.read_text().splitlines()[-40:]
    urls = [w for line in tail for w in line.split() if w.startswith("http")]
    return urls[-1] if urls else None


def _ingest_record(kind: str, label: str) -> dict:
    ingest_id = uuid.uuid4().hex[:12]
    record = {"ingest_id": ingest_id, "kind": kind, "label": label,
              "status": "queued", "created_at": time.time(), "log": [],
              "manifest": None, "error": None}
    with _ingest_lock:
        _ingests[ingest_id] = record
        if len(_ingests) > INGEST_KEEP:
            for stale in sorted(_ingests.values(), key=lambda r: r["created_at"])[:-INGEST_KEEP]:
                _ingests.pop(stale["ingest_id"], None)
    return record


def _ingest_log(ingest_id: str, message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(f"ingest {ingest_id}: {message}", flush=True)
    with _ingest_lock:
        record = _ingests.get(ingest_id)
        if record is not None:
            record["log"].append(line)
            del record["log"][:-60]


def _ingest_update(ingest_id: str, **fields) -> None:
    with _ingest_lock:
        _ingests[ingest_id].update(fields)


def _run_ingest(ingest_id: str, work) -> None:
    with _ingest_slots:
        _ingest_update(ingest_id, status="running", started_at=time.time())
        try:
            manifest = work(lambda msg: _ingest_log(ingest_id, msg))
        except ingest.IngestError as exc:
            _ingest_log(ingest_id, f"failed: {exc}")
            _ingest_update(ingest_id, status="failed", error=str(exc),
                           ended_at=time.time())
        except Exception as exc:  # noqa: BLE001 - never lose the thread silently
            _ingest_log(ingest_id, f"failed: {type(exc).__name__}: {exc}")
            _ingest_update(ingest_id, status="failed",
                           error=f"{type(exc).__name__}: {exc}", ended_at=time.time())
        else:
            _ingest_update(ingest_id, status="succeeded", manifest=manifest,
                           ended_at=time.time())


def _start_ingest(kind: str, label: str, work) -> dict:
    record = _ingest_record(kind, label)
    threading.Thread(target=_run_ingest, args=(record["ingest_id"], work),
                     daemon=True, name=f"ingest-{record['ingest_id']}").start()
    return {"ingest_id": record["ingest_id"], "status": "queued", "label": label}


class FetchRequest(BaseModel):
    query: str
    scan_figures: bool = True
    max_figures: int = 10


@app.post("/papers/fetch", status_code=202, dependencies=[Depends(require_token)])
def fetch_paper(req: FetchRequest) -> dict:
    """Pull a paper in from arXiv by id, URL or title and scan it."""
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    max_figures = max(0, min(req.max_figures, 20))

    def work(log):
        return ingest.ingest_arxiv(query, papers_dir=PAPERS,
                                   scan_figures=req.scan_figures,
                                   max_figures=max_figures, log=log)

    return _start_ingest("arxiv", query, work)


@app.post("/papers/upload", status_code=202, dependencies=[Depends(require_token)])
async def upload_paper(request: Request, title: str | None = None,
                       scan_figures: bool = True, max_figures: int = 10) -> dict:
    """Ingest a PDF posted as the raw request body.

    Raw bytes rather than multipart so the same call works through the Vercel edge
    proxy, the express proxy and the local stdlib API without a form parser.
    """
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="body is not a PDF")
    if len(data) > ingest.MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 40 MB cap")
    hint = (request.headers.get("x-paper-title") or "").strip() or None
    label = (title or hint or "uploaded PDF").strip()
    capped = max(0, min(max_figures, 20))

    def work(log):
        return ingest.ingest_pdf(data, papers_dir=PAPERS, title=title,
                                 title_hint=hint, source="upload",
                                 scan_figures=scan_figures, max_figures=capped,
                                 log=log)

    return _start_ingest("upload", label, work)


@app.get("/papers/ingests")
def list_ingests() -> list[dict]:
    with _ingest_lock:
        return sorted(_ingests.values(), key=lambda r: r["created_at"], reverse=True)


@app.get("/papers/ingests/{ingest_id}")
def get_ingest(ingest_id: str) -> dict:
    with _ingest_lock:
        record = _ingests.get(ingest_id)
    if not record:
        raise HTTPException(status_code=404, detail="no such ingest")
    return record


@app.get("/papers/{slug}")
def get_paper(slug: str) -> dict:
    paper_dir = _resolve_paper(f"papers/{slug}")
    if not (paper_dir / "paper.json").is_file():
        raise HTTPException(status_code=404, detail="no such paper")
    return ingest.manifest(paper_dir, papers_dir=PAPERS)


@app.get("/papers/{slug}/figures/{name}")
def get_figure(slug: str, name: str) -> Response:
    paper_dir = _resolve_paper(f"papers/{slug}")
    target = (paper_dir / "figures" / name).resolve()
    if paper_dir.resolve() not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="no such figure")
    return Response(content=target.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


class RunRequest(BaseModel):
    paper_dir: str = "papers/fashion-mnist"
    seeds: str = "17,41,93"
    publish: bool = False


@app.get("/healthz")
def healthz() -> dict:
    from repro.env import env_key
    return {
        "ok": True,
        "queue_depth": _q.qsize(),
        "keys": {
            "zai": bool(env_key("ZAI_API_KEY", "ZAI_API")),
            "daytona": bool(env_key("DAYTONA_API_KEY", "DAYTONA_API")),
            "parallel": bool(env_key("PARALLEL_API_KEY", "PARALLEL_API")),
        },
    }


@app.get("/papers")
def papers(detail: bool = True):
    """Every paper the pipeline can be pointed at.

    `detail=false` returns the bare paper_dir list this endpoint used to return,
    for any client pinned to the older shape.
    """
    rows = ingest.list_papers(PAPERS)
    if not detail:
        return [row["paper_dir"] for row in rows]
    return rows


@app.post("/runs", status_code=202, dependencies=[Depends(require_token)])
def create_run(req: RunRequest) -> dict:
    _resolve_paper(req.paper_dir)
    if not SEEDS_RE.match(req.seeds):
        raise HTTPException(status_code=400, detail="seeds must be comma-separated integers")
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"job_id": job_id, "status": "queued", "paper_dir": req.paper_dir,
                         "seeds": req.seeds, "publish": req.publish,
                         "created_at": time.time(), "run_id": None}
    _save()
    _q.put(job_id)
    return {"job_id": job_id, "status": "queued", "queue_depth": _q.qsize()}


@app.get("/runs")
def list_runs() -> list[dict]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)


@app.get("/runs/{job_id}")
def get_run(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    out = dict(job)
    log_path = RUN_ROOT / f"{job_id}.log"
    if log_path.is_file():
        out["log_tail"] = log_path.read_text().splitlines()[-30:]
    if job.get("run_id"):
        verdicts = RUN_ROOT / job["run_id"] / "verdicts.json"
        if verdicts.is_file():
            parsed = json.loads(verdicts.read_text())
            out["verdicts"] = parsed
            # a degraded run measured generated data, so its verdicts carry
            # 'NOT COMPARABLE' and the real grade sits in graded_verdict_withheld
            out["degraded"] = bool(parsed.get("degraded"))
        out["has_report"] = (RUN_ROOT / job["run_id"] / "report.md").is_file()
        out["feed_url"] = f"/runs/{job_id}/feed"
    return out


LEDGER = RUN_ROOT / "ledger.db"


def _run_id_for(job_id: str, wait_s: float = 0.0) -> str | None:
    """A job is queued before its run exists, so a viewer who opens the feed early has
    to be waited for rather than refused."""
    deadline = time.time() + wait_s
    while True:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="no such job")
        if job.get("run_id"):
            return job["run_id"]
        if time.time() >= deadline:
            return None
        time.sleep(1)


@app.get("/runs/{job_id}/feed", response_class=HTMLResponse)
def run_feed(job_id: str) -> str:
    """The live feed for one run: activity, attempt grid, gates, verdicts, timings."""
    from repro import feed

    _run_id_for(job_id)  # 404s on an unknown job
    return feed.PAGE


@app.get("/runs/{job_id}/events")
def run_events(job_id: str, after: int = 0, replay: str | None = None,
               speed: float = 1.0):
    """The run's event stream, as server-sent events.

    The run is a separate process, so this tails its ledger rather than sharing a bus -
    the same path `repro feed` uses to replay a finished run.
    """
    from repro import feed

    run_id = _run_id_for(job_id, wait_s=30)
    if run_id is None:
        raise HTTPException(status_code=409, detail="run has not started yet; retry")
    if not LEDGER.is_file():
        raise HTTPException(status_code=404, detail="no ledger for this run yet")
    return StreamingResponse(
        feed.iter_frames(str(LEDGER), run_id, after=after,
                         paced=(replay == "paced"), speed=max(1.0, speed),
                         idle_timeout=900),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/runs/{job_id}/report", response_class=PlainTextResponse)
def get_report(job_id: str) -> str:
    job = _jobs.get(job_id)
    if not job or not job.get("run_id"):
        raise HTTPException(status_code=404, detail="no such job")
    report = RUN_ROOT / job["run_id"] / "report.md"
    if not report.is_file():
        raise HTTPException(status_code=404, detail="no report for this run")
    return report.read_text()


_load()
threading.Thread(target=_worker, daemon=True).start()

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

from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

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
def papers() -> list[str]:
    return sorted(f"papers/{p.name}" for p in PAPERS.iterdir()
                  if p.is_dir() and (p / "paper.json").is_file())


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
    return out


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

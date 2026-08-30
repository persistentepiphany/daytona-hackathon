"""HTTP API that drives the autonomous pipeline for the Snapshot frontend.

Stdlib only. Jobs run in background threads; the UI polls `/api/runs/<id>`.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import ingest

ROOT = Path(__file__).resolve().parents[1]
PAPERS = ROOT / "papers"
RUN_ROOT = ROOT / "runs" / "auto"
RESULTS_AUTO = ROOT / "results" / "auto"
INBOX = PAPERS / "_inbox"

# stage keys the UI renders as Activity rows
STAGE_ORDER = ("intake", "planner", "freeze", "build", "experiments", "verdicts")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:48] or "paper") + f"-{int(time.time())}"


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self._hydrate_disk()

    def _hydrate_disk(self) -> None:
        """Surface finished local + committed runs so the sidebar is never empty."""
        for base in (RUN_ROOT, RESULTS_AUTO):
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir(), reverse=True):
                if not d.is_dir() or not d.name.startswith("auto-"):
                    continue
                if d.name in self.jobs:
                    continue
                verdicts = _read_json(d / "verdicts.json")
                paper_meta = _guess_paper_meta(d)
                self.jobs[d.name] = {
                    "run_id": d.name,
                    "status": "completed" if verdicts else "failed",
                    "created_at": _mtime_iso(d),
                    "updated_at": _mtime_iso(d),
                    "paper_slug": paper_meta.get("paper_id"),
                    "title": paper_meta.get("title") or d.name,
                    "paper_dir": None,
                    "exit_code": 0 if verdicts and verdicts.get("verdicts") else 3,
                    "logs": [],
                    "stages": _stages_from_disk(d, verdicts),
                    "verdicts": verdicts,
                    "report": _read_text(d / "report.md"),
                    "error": None,
                    "source": "disk",
                }

    def create(self, *, title: str, paper_slug: str | None, paper_dir: str,
               message: str) -> dict[str, Any]:
        run_id = f"auto-{int(time.time())}"
        job = {
            "run_id": run_id,
            "status": "queued",
            "created_at": _now(),
            "updated_at": _now(),
            "paper_slug": paper_slug,
            "title": title,
            "paper_dir": paper_dir,
            "message": message,
            "exit_code": None,
            "logs": [],
            "stages": {k: {"status": "pending", "detail": ""} for k in STAGE_ORDER},
            "verdicts": None,
            "report": None,
            "error": None,
            "source": "live",
        }
        with self._lock:
            self.jobs[run_id] = job
        return job

    def update(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            job = self.jobs[run_id]
            job.update(fields)
            job["updated_at"] = _now()

    def append_log(self, run_id: str, line: str) -> None:
        with self._lock:
            job = self.jobs[run_id]
            job["logs"].append(line)
            if len(job["logs"]) > 400:
                job["logs"] = job["logs"][-400:]
            job["updated_at"] = _now()
            _apply_stage(job, line)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self.jobs.get(run_id)
            return json.loads(json.dumps(job)) if job else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)
            return [
                {
                    "run_id": j["run_id"],
                    "status": j["status"],
                    "title": j["title"],
                    "paper_slug": j.get("paper_slug"),
                    "created_at": j["created_at"],
                    "source": j.get("source"),
                }
                for j in rows
            ]


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _read_text(path: Path) -> str | None:
    return path.read_text() if path.is_file() else None


def _mtime_iso(path: Path) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))


def _guess_paper_meta(run_dir: Path) -> dict:
    prereg = _read_json(run_dir / "prereg.json") or {}
    paper = prereg.get("paper") or {}
    if paper.get("title"):
        return paper
    # fall back to known paper dirs by scanning titles in papers/
    return {"paper_id": None, "title": run_dir.name}


def _stages_from_disk(run_dir: Path, verdicts: Any) -> dict[str, dict[str, str]]:
    stages = {k: {"status": "pending", "detail": ""} for k in STAGE_ORDER}
    if (run_dir / "prereg.json").is_file():
        for k in ("intake", "planner", "freeze"):
            stages[k] = {"status": "done", "detail": "from disk"}
    if (run_dir / "build.json").is_file():
        stages["build"] = {"status": "done", "detail": "from disk"}
    if (run_dir / "evidence").is_dir() and any((run_dir / "evidence").iterdir()):
        stages["experiments"] = {"status": "done", "detail": "from disk"}
    if verdicts and verdicts.get("verdicts") is not None:
        stages["verdicts"] = {"status": "done", "detail": f"{len(verdicts['verdicts'])} rows"}
        for k in STAGE_ORDER:
            if stages[k]["status"] == "pending":
                stages[k] = {"status": "done", "detail": "from disk"}
    return stages


def _apply_stage(job: dict[str, Any], line: str) -> None:
    stages = job["stages"]
    plain = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", line)

    def mark(key: str, status: str, detail: str) -> None:
        stages[key] = {"status": status, "detail": detail[:180]}
        # mark prior stages done
        for earlier in STAGE_ORDER:
            if earlier == key:
                break
            if stages[earlier]["status"] == "pending":
                stages[earlier] = {"status": "done", "detail": stages[earlier]["detail"] or "ok"}

    if plain.startswith("P0"):
        mark("intake", "running" if "FAILED" not in plain else "failed", plain)
        if "code gate" in plain:
            mark("intake", "done", plain)
    elif plain.startswith("planner:") or plain.startswith("contract:"):
        mark("planner", "done" if plain.startswith("contract:") else "running", plain)
    elif plain.startswith("G1"):
        mark("freeze", "done", plain)
    elif plain.startswith("P1"):
        status = "failed" if "FAILED" in plain else ("done" if "S0 frozen" in plain else "running")
        mark("build", status, plain)
    elif plain.startswith("P2"):
        mark("experiments", "running", plain)
    elif plain.startswith("P3"):
        mark("verdicts", "running", plain)
    elif plain.startswith("done:"):
        for k in STAGE_ORDER:
            if stages[k]["status"] in ("pending", "running"):
                stages[k] = {"status": "done", "detail": stages[k]["detail"] or "ok"}


STORE = JobStore()


def list_papers() -> list[dict[str, Any]]:
    """Same manifest shape the Render API serves, so one client covers both."""
    return ingest.list_papers(PAPERS)


class IngestStore:
    """Ingestion jobs: seconds-to-a-minute of work, kept off the run path."""

    KEEP = 40

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self._slots = threading.Semaphore(2)

    def start(self, kind: str, label: str, work) -> dict[str, Any]:
        ingest_id = uuid.uuid4().hex[:12]
        record = {"ingest_id": ingest_id, "kind": kind, "label": label,
                  "status": "queued", "created_at": time.time(), "log": [],
                  "manifest": None, "error": None}
        with self._lock:
            self.jobs[ingest_id] = record
            for stale in sorted(self.jobs.values(),
                                key=lambda r: r["created_at"])[:-self.KEEP]:
                self.jobs.pop(stale["ingest_id"], None)
        threading.Thread(target=self._run, args=(ingest_id, work), daemon=True,
                         name=f"ingest-{ingest_id}").start()
        return {"ingest_id": ingest_id, "status": "queued", "label": label}

    def _log(self, ingest_id: str, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        with self._lock:
            record = self.jobs.get(ingest_id)
            if record is not None:
                record["log"].append(line)
                del record["log"][:-60]

    def _update(self, ingest_id: str, **fields: Any) -> None:
        with self._lock:
            self.jobs[ingest_id].update(fields)

    def _run(self, ingest_id: str, work) -> None:
        with self._slots:
            self._update(ingest_id, status="running", started_at=time.time())
            try:
                manifest = work(lambda msg: self._log(ingest_id, msg))
            except ingest.IngestError as exc:
                self._log(ingest_id, f"failed: {exc}")
                self._update(ingest_id, status="failed", error=str(exc),
                             ended_at=time.time())
            except Exception as exc:  # noqa: BLE001 - never lose the thread silently
                self._log(ingest_id, f"failed: {type(exc).__name__}: {exc}")
                self._update(ingest_id, status="failed",
                             error=f"{type(exc).__name__}: {exc}", ended_at=time.time())
            else:
                self._update(ingest_id, status="succeeded", manifest=manifest,
                             ended_at=time.time())

    def get(self, ingest_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self.jobs.get(ingest_id)
            return json.loads(json.dumps(record)) if record else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted((json.loads(json.dumps(r)) for r in self.jobs.values()),
                          key=lambda r: r["created_at"], reverse=True)


INGESTS = IngestStore()


def start_arxiv_fetch(body: dict[str, Any]) -> dict[str, Any]:
    query = (body.get("query") or body.get("arxiv") or body.get("message") or "").strip()
    if not query:
        raise ValueError("query is required (an arXiv id, URL or title)")
    scan = body.get("scan_figures", True)
    max_figures = max(0, min(int(body.get("max_figures") or 10), 20))

    def work(log):
        return ingest.ingest_arxiv(query, papers_dir=PAPERS, scan_figures=bool(scan),
                                   max_figures=max_figures, log=log)

    return INGESTS.start("arxiv", query, work)


def start_pdf_upload(data: bytes, *, title: str | None = None,
                     title_hint: str | None = None, scan_figures: bool = True,
                     max_figures: int = 10) -> dict[str, Any]:
    if not data:
        raise ValueError("empty upload")
    if not data.startswith(b"%PDF"):
        raise ValueError("body is not a PDF")
    if len(data) > ingest.MAX_PDF_BYTES:
        raise ValueError("PDF exceeds the 40 MB cap")
    label = (title or title_hint or "uploaded PDF").strip()
    capped = max(0, min(max_figures, 20))

    def work(log):
        return ingest.ingest_pdf(data, papers_dir=PAPERS, title=title,
                                 title_hint=title_hint, source="upload",
                                 scan_figures=scan_figures, max_figures=capped,
                                 log=log)

    return INGESTS.start("upload", label, work)


def read_figure(slug: str, name: str) -> bytes | None:
    base = (PAPERS / slug).resolve()
    if PAPERS.resolve() not in base.parents:
        return None
    target = (base / "figures" / name).resolve()
    if base not in target.parents or not target.is_file():
        return None
    return target.read_bytes()


def materialize_paper(*, text: str, title: str | None = None,
                      slug: str | None = None) -> tuple[Path, dict]:
    """Write a paper dir the autonomous driver can consume."""
    INBOX.mkdir(parents=True, exist_ok=True)
    paper_id = slug or _slugify(title or text[:40])
    dest = INBOX / paper_id
    dest.mkdir(parents=True, exist_ok=True)
    title = (title or "User-submitted paper").strip() or "User-submitted paper"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    meta = {
        "paper_id": paper_id,
        "title": title,
        "authors": [],
        "pdf_sha256": digest,
        "role": "user",
        "notes": "Materialized from Snapshot chat intake",
    }
    (dest / "paper-extract.txt").write_text(text)
    (dest / "paper.json").write_text(json.dumps(meta, indent=2))
    (dest / "code_absence.json").write_text(json.dumps({
        "title": title,
        "queries": [],
        "results": [],
    }, indent=2))
    return dest, meta


def resolve_paper_request(body: dict[str, Any]) -> tuple[Path, dict, str]:
    """Return (paper_dir, meta, display_title) from a chat/API payload."""
    message = (body.get("message") or "").strip()
    paper_slug = (body.get("paper_slug") or "").strip() or None
    paper_text = (body.get("paper_text") or "").strip() or None
    title = (body.get("title") or "").strip() or None

    if paper_slug:
        d = PAPERS / paper_slug
        if not (d / "paper-extract.txt").is_file():
            raise ValueError(f"unknown paper_slug: {paper_slug}")
        # ensure metadata exists for incomplete paper dirs
        if not (d / "paper.json").is_file() or not (d / "code_absence.json").is_file():
            text = (d / "paper-extract.txt").read_text()
            materialize_into(d, text, title=title)
        meta = json.loads((d / "paper.json").read_text())
        return d, meta, meta.get("title") or paper_slug

    # long paste → treat as paper body
    if paper_text or (message and len(message) >= 400):
        text = paper_text or message
        guessed = title or _guess_title(text)
        dest, meta = materialize_paper(text=text, title=guessed)
        return dest, meta, meta["title"]

    # short message → fuzzy match known papers
    if message:
        hit = _match_paper(message)
        if hit:
            d = PAPERS / hit
            meta = json.loads((d / "paper.json").read_text()) if (d / "paper.json").is_file() else {
                "paper_id": hit, "title": hit, "pdf_sha256": "0" * 64}
            if not (d / "paper.json").is_file():
                materialize_into(d, (d / "paper-extract.txt").read_text(), title=meta.get("title"))
                meta = json.loads((d / "paper.json").read_text())
            return d, meta, meta.get("title") or hit

    raise ValueError(
        "Provide a paper_slug, paste the paper text (≥400 chars), "
        "or name a known paper (e.g. fashion-mnist)."
    )


def materialize_into(dest: Path, text: str, title: str | None = None) -> None:
    title = title or dest.name
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if not (dest / "paper.json").is_file():
        (dest / "paper.json").write_text(json.dumps({
            "paper_id": dest.name,
            "title": title,
            "authors": [],
            "pdf_sha256": digest,
            "role": "user",
        }, indent=2))
    if not (dest / "code_absence.json").is_file():
        (dest / "code_absence.json").write_text(json.dumps({
            "title": title, "queries": [], "results": [],
        }, indent=2))


def _guess_title(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if 12 <= len(line) <= 160 and not line.lower().startswith("abstract"):
            return line
    return "User-submitted paper"


def _match_paper(message: str) -> str | None:
    m = message.lower()
    aliases = {
        "fashion-mnist": ["fashion-mnist", "fashion mnist", "1708.07747", "zalando"],
        "dnn-pattern-recognition": [
            "dnn-pattern-recognition", "pattern recognition", "1905.11028",
            "best-scored", "random forest classification", "kyongsik",
        ],
    }
    for slug, keys in aliases.items():
        if any(k in m for k in keys) and (PAPERS / slug / "paper-extract.txt").is_file():
            return slug
    # exact slug token
    for d in PAPERS.iterdir() if PAPERS.is_dir() else []:
        if d.is_dir() and d.name in m and (d / "paper-extract.txt").is_file():
            return d.name
    return None


def start_run(body: dict[str, Any]) -> dict[str, Any]:
    paper_dir, meta, title = resolve_paper_request(body)
    seeds_raw = body.get("seeds") or "17,41,93"
    seeds = [int(s) for s in str(seeds_raw).split(",") if str(s).strip()]
    message = (body.get("message") or body.get("paper_text") or title).strip()
    job = STORE.create(
        title=title,
        paper_slug=meta.get("paper_id") or paper_dir.name,
        paper_dir=str(paper_dir),
        message=message,
    )
    thread = threading.Thread(
        target=_execute_job,
        args=(job["run_id"], paper_dir, seeds),
        daemon=True,
        name=f"run-{job['run_id']}",
    )
    thread.start()
    return {"run_id": job["run_id"], "title": title, "status": "queued",
            "paper_slug": job["paper_slug"]}


def _load_run_auto():
    import importlib.util
    import sys

    sys.path.insert(0, str(ROOT))
    path = ROOT / "scripts" / "auto_run.py"
    spec = importlib.util.spec_from_file_location("auto_run_mod", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_auto


def _execute_job(run_id: str, paper_dir: Path, seeds: list[int]) -> None:
    STORE.update(run_id, status="running")
    STORE.append_log(run_id, f"starting autonomous run for {paper_dir}")

    def log(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        STORE.append_log(run_id, line)

    try:
        import os
        os.chdir(ROOT)
        run_auto = _load_run_auto()
        code = run_auto(paper_dir, seeds=seeds, run_id=run_id, log=log)
        run_dir = RUN_ROOT / run_id
        verdicts = _read_json(run_dir / "verdicts.json")
        report = _read_text(run_dir / "report.md")
        status = "completed" if code == 0 else "failed"
        STORE.update(run_id, status=status, exit_code=code,
                     verdicts=verdicts, report=report)
        if code != 0:
            STORE.append_log(run_id, f"finished with exit code {code}")
    except Exception as exc:  # noqa: BLE001
        STORE.append_log(run_id, f"ERROR: {exc}")
        STORE.append_log(run_id, traceback.format_exc()[-1500:])
        STORE.update(run_id, status="failed", error=str(exc), exit_code=1)


def make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _send(self, body: bytes, ctype: str = "application/json", code: int = 200) -> None:
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200) -> None:
            self._send(json.dumps(obj).encode(), code=code)

        def _query(self) -> dict[str, list[str]]:
            return parse_qs(urlparse(self.path).query)

        def _flag(self, name: str, default: bool) -> bool:
            raw = (self._query().get(name) or [None])[0]
            if raw is None:
                return default
            return raw.lower() not in ("0", "false", "no")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            path = unquote(url.path)
            if path in ("/api/health", "/health"):
                return self._json({"ok": True, "service": "snapshot-repro-api"})
            if path == "/api/papers":
                rows = list_papers()
                if not self._flag("detail", True):
                    return self._json([r["paper_dir"] for r in rows])
                return self._json(rows)
            if path == "/api/papers/ingests":
                return self._json(INGESTS.list())
            if path.startswith("/api/papers/ingests/"):
                record = INGESTS.get(path[len("/api/papers/ingests/"):].strip("/"))
                if not record:
                    return self._json({"error": "no such ingest"}, 404)
                return self._json(record)
            figure = re.match(r"^/api/papers/([^/]+)/figures/([^/]+)$", path)
            if figure:
                png = read_figure(figure.group(1), figure.group(2))
                if png is None:
                    return self._json({"error": "no such figure"}, 404)
                return self._send(png, ctype="image/png")
            if path.startswith("/api/papers/"):
                slug = path[len("/api/papers/"):].strip("/")
                d = PAPERS / slug
                if "/" in slug or not (d / "paper.json").is_file():
                    return self._json({"error": "no such paper"}, 404)
                return self._json(ingest.manifest(d, papers_dir=PAPERS))
            if path == "/api/runs":
                return self._json(STORE.list())
            if path.startswith("/api/runs/"):
                run_id = path[len("/api/runs/"):].strip("/")
                job = STORE.get(run_id)
                if not job:
                    return self._json({"error": "not found"}, 404)
                return self._json(job)
            return self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            path = unquote(url.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > ingest.MAX_PDF_BYTES + 4096:
                return self._json({"error": "upload exceeds the 40 MB cap"}, 413)
            raw = self.rfile.read(length) if length else b"{}"

            # the PDF arrives as raw bytes, not multipart, so the same request
            # shape works through the express and edge proxies unchanged
            if path == "/api/papers/upload":
                explicit = (self._query().get("title") or [None])[0]
                hint = self.headers.get("X-Paper-Title")
                try:
                    return self._json(start_pdf_upload(
                        raw,
                        title=explicit.strip() if explicit else None,
                        title_hint=hint.strip() if hint else None,
                        scan_figures=self._flag("scan_figures", True),
                        max_figures=int((self._query().get("max_figures") or [10])[0]),
                    ), 202)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
            try:
                body = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                return self._json({"error": "invalid json"}, 400)

            if path == "/api/papers/fetch":
                try:
                    return self._json(
                        start_arxiv_fetch(body if isinstance(body, dict) else {}), 202)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)

            if path == "/api/runs":
                try:
                    result = start_run(body if isinstance(body, dict) else {})
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    return self._json({"error": str(exc)}, 500)
                return self._json(result, 202)

            return self._json({"error": "not found"}, 404)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), make_handler())
    print(f"snapshot api on http://{host}:{port}", flush=True)
    server.serve_forever()

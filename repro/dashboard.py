"""Local run dashboard: one small stdlib server reading the ledger SQLite directly.

Run grid, per-experiment attempt status, verdict table, and links to evidence
files. Served by the orchestrator on localhost; no hosting, no build framework.
"""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Run ledger</title>
<style>
 body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 72rem; }
 table { border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }
 td, th { border: 1px solid #ccc; padding: .35rem .6rem; text-align: left; font-size: .9rem; }
 h2 { margin-top: 2rem; } tr.ok { background:#e6f4e6; } tr.bad { background:#f8e0e0; }
 tr.mid { background:#fdf3d8; } a { color: #0645ad; } code { font-size: .8rem; }
</style>
<h1>Preregistered runs</h1>
<div id="runs"></div>
<div id="detail"></div>
<script>
const cell = t => { const td = document.createElement("td"); td.textContent = t ?? ""; return td; };
const row = (vals, cls) => { const tr = document.createElement("tr"); if (cls) tr.className = cls;
  vals.forEach(v => tr.appendChild(v instanceof Node ? v : cell(v))); return tr; };
const table = (heads, rows) => { const t = document.createElement("table");
  t.appendChild(row(heads)); rows.forEach(r => t.appendChild(r)); return t; };

async function loadRuns() {
  const runs = await (await fetch("/api/runs")).json();
  const rows = runs.map(r => {
    const a = document.createElement("a");
    a.href = "#"; a.textContent = r.run_id;
    a.onclick = () => { loadRun(r.run_id); return false; };
    return row([a, (r.prereg_hash || "").slice(0, 16), r.s0_snapshot,
                r.attempts, r.verdicts]);
  });
  const div = document.getElementById("runs");
  div.innerHTML = "";
  div.appendChild(table(["run", "prereg", "S0", "attempts", "verdicts"], rows));
}

async function loadRun(id) {
  const d = await (await fetch("/api/run?id=" + encodeURIComponent(id))).json();
  const div = document.getElementById("detail");
  div.innerHTML = "<h2>" + id + "</h2>";

  const att = d.attempts.map(a => {
    const link = document.createElement("a");
    link.href = "/evidence?run=" + encodeURIComponent(id) + "&exp=" + encodeURIComponent(a.exp_id);
    link.textContent = a.exp_id;
    const status = a.ended == null ? "running" : (a.exit === 0 ? "done" : "failed");
    return row([link, a.claim_id, a.attempt_id, a.sandbox_id, status,
                (a.evidence_sha || "").slice(0, 12)],
               status === "done" ? "ok" : (status === "failed" ? "bad" : "mid"));
  });
  div.appendChild(table(["experiment", "claim", "attempt", "sandbox", "status", "evidence"], att));

  const ver = d.verdicts.map(v =>
    row([v.claim_id, v.rule_id, v.observed, v.delta, v.verdict],
        v.verdict.includes("WITHIN") || v.verdict.includes("PASS") ? "ok"
        : (v.verdict.includes("NOT") || v.verdict.includes("FAIL") ? "bad" : "mid")));
  div.appendChild(table(["claim", "rule", "observed", "delta", "verdict"], ver));
}
loadRuns();
</script>
"""


def runs_payload(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT r.run_id, r.prereg_hash, r.s0_snapshot,"
        " (SELECT COUNT(*) FROM attempts a WHERE a.run_id = r.run_id) AS attempts,"
        " (SELECT COUNT(*) FROM verdicts v WHERE v.run_id = r.run_id) AS verdicts"
        " FROM runs r ORDER BY r.created_at DESC").fetchall()
    return [dict(r) for r in rows]


def run_payload(db: sqlite3.Connection, run_id: str) -> dict:
    attempts = [dict(r) for r in db.execute(
        "SELECT attempt_id, exp_id, claim_id, sandbox_id, started, ended, exit, evidence_sha"
        " FROM attempts WHERE run_id=? ORDER BY started", (run_id,))]
    verdicts = [dict(r) for r in db.execute(
        "SELECT claim_id, rule_id, observed, delta, verdict FROM verdicts"
        " WHERE run_id=? ORDER BY created_at", (run_id,))]
    return {"run_id": run_id, "attempts": attempts, "verdicts": verdicts}


def find_evidence_dir(evidence_root: Path, run_id: str, exp_id: str) -> Path | None:
    for candidate in (evidence_root / run_id / "evidence" / exp_id,
                      evidence_root / "evidence" / exp_id,
                      evidence_root / exp_id):
        if candidate.is_dir():
            return candidate
    matches = list(evidence_root.glob(f"**/{run_id}/evidence/{exp_id}"))
    return matches[0] if matches else None


def make_handler(ledger_path: str, evidence_root: str):
    root = Path(evidence_root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _send(self, body: bytes, ctype: str = "application/json", code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            db = sqlite3.connect(ledger_path)
            db.row_factory = sqlite3.Row
            try:
                if url.path == "/":
                    self._send(PAGE.encode(), "text/html; charset=utf-8")
                elif url.path == "/api/runs":
                    self._send(json.dumps(runs_payload(db)).encode())
                elif url.path == "/api/run":
                    run_id = parse_qs(url.query).get("id", [""])[0]
                    self._send(json.dumps(run_payload(db, run_id)).encode())
                elif url.path == "/evidence":
                    q = parse_qs(url.query)
                    d = find_evidence_dir(root, q.get("run", [""])[0], q.get("exp", [""])[0])
                    if d is None:
                        self._send(b"no evidence directory", "text/plain", 404)
                        return
                    listing = {f.name: f.read_text()[:20000] for f in sorted(d.iterdir())
                               if f.is_file()}
                    self._send(json.dumps(listing, indent=1).encode())
                else:
                    self._send(b"not found", "text/plain", 404)
            finally:
                db.close()

    return Handler


def serve(ledger_path: str, evidence_root: str, port: int = 8600) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ledger_path, evidence_root))
    print(f"dashboard on http://127.0.0.1:{port} (ledger: {ledger_path})")
    server.serve_forever()

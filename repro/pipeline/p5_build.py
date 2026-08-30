"""P5 thin build: build what survived, not what was claimed.

One API endpoint plus one static page in a container sandbox, exposed via a preview
link. A deterministic fallback builder renders the verdict table without any LLM,
so the demo never depends on a model key; the Builder role can replace the files
when available. G3 gates any push of the output.
"""

import json

from ..orchestrator.adapter import SandboxAdapter
from ..orchestrator.ledger import Ledger
from ..orchestrator.lifecycle import Lifecycle

APP_DIR = "/home/daytona/app"
PORT = 8000


class PushNotApproved(RuntimeError):
    pass


def push_output(gates, run_id: str, push_fn) -> None:
    """G3: any push of the output requires explicit prior approval. push_fn is the
    actual push callable; it never runs without the recorded gate."""
    if not gates.passed(run_id, "G3"):
        raise PushNotApproved(f"G3 not approved for run {run_id}; refusing to push")
    push_fn()


def fallback_app_files(brief_rows: list[dict], hermeticity: str, paper_title: str,
                       lineage: dict | None = None) -> dict[str, str]:
    """Deterministic thin app: /api/verdicts plus a static page, standard library only."""
    payload = json.dumps({"paper": paper_title, "hermeticity": hermeticity,
                          "lineage": lineage or {}, "verdicts": brief_rows}, indent=2)
    app_py = f'''"""One-endpoint API serving the validated-knowledge brief."""

import http.server
import json

DATA = json.loads(r\'\'\'{payload}\'\'\')


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") == "/api/verdicts":
            body = json.dumps(DATA).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.path = "/index.html"
            super().do_GET()


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("0.0.0.0", {PORT}), Handler).serve_forever()
'''
    index_html = """<!doctype html>
<meta charset="utf-8">
<title>What survived</title>
<style>
 body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 60rem; }
 table { border-collapse: collapse; width: 100%; }
 td, th { border: 1px solid #ccc; padding: .4rem .6rem; text-align: left; }
 .ok { background: #e6f4e6; } .bad { background: #f8e0e0; } .mid { background: #fdf3d8; }
 caption { font-weight: 600; margin-bottom: .5rem; text-align: left; }
</style>
<h1>Build what survived</h1>
<p id="sub"></p>
<p id="lineage" style="font-family: monospace; font-size: .8rem; color: #555;"></p>
<table id="t"><caption>Executed verdicts, from the ledger</caption>
<tr><th>Experiment</th><th>Claim</th><th>Type</th><th>Observed</th><th>Delta</th><th>Verdict</th></tr>
</table>
<p id="herm"></p>
<script>
fetch("/api/verdicts").then(r => r.json()).then(d => {
  document.getElementById("sub").textContent = d.paper;
  document.getElementById("herm").textContent = "Hermeticity: " + d.hermeticity;
  const lin = d.lineage || {};
  document.getElementById("lineage").textContent = Object.entries(lin)
    .map(([k, v]) => k + "=" + String(v).slice(0, 20)).join("  ");
  const t = document.getElementById("t");
  for (const v of d.verdicts) {
    const tr = document.createElement("tr");
    tr.className = v.verdict === "REPRODUCED WITHIN TOLERANCE" || v.verdict === "CONTROL PASS"
      ? "ok" : (v.verdict === "NOT REPRODUCED" || v.verdict === "CONTROL FAIL" ? "bad" : "mid");
    for (const k of ["experiment_id", "claim_id", "type", "observed", "delta", "verdict"]) {
      const td = document.createElement("td");
      td.textContent = v[k];
      tr.appendChild(td);
    }
    t.appendChild(tr);
  }
});
</script>
"""
    return {"app.py": app_py, "index.html": index_html}


def deploy(life: Lifecycle, adapter: SandboxAdapter, ledger: Ledger, run_id: str,
           files: dict[str, str], start_command: str = "python3 app.py",
           base_snapshot: str = "daytona-small", demo_window: bool = False) -> dict:
    kind = "build_demo" if demo_window else "build"
    sid = life.create(kind, name=f"build-{run_id}", snapshot=base_snapshot)
    r = adapter.exec(sid, f"mkdir -p {APP_DIR}")
    if r.exit_code != 0:
        raise RuntimeError(f"cannot create app dir: {r.output}")
    for name, content in files.items():
        adapter.write_file(sid, f"{APP_DIR}/{name}", content.encode())
    r = adapter.exec(sid, f"nohup {start_command} > server.log 2>&1 & sleep 1; "
                          f"curl -sS -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{PORT}/api/verdicts",
                     cwd=APP_DIR, timeout=60)
    if r.exit_code != 0 or not r.output.strip().endswith("200"):
        log = adapter.exec(sid, f"cat {APP_DIR}/server.log").output[-1000:]
        raise RuntimeError(f"app failed to start ({r.output.strip()}):\n{log}")
    preview = adapter.preview_url(sid, PORT)
    signed = adapter.signed_preview_url(sid, PORT, expires_in_seconds=86400)  # 24h is the max
    ledger.log_event(run_id, "build_deployed", {
        "sandbox_id": sid, "preview_url": preview, "signed_url": signed,
    })
    return {"sandbox_id": sid, "preview_url": preview, "signed_url": signed}

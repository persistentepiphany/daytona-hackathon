"""Builder: builds what survived, not what was claimed.

Receives only the validated-knowledge brief — claims with their verdicts — never
the paper. Scope is hard-capped to one API endpoint plus one static page, served
from a container sandbox and exposed via a preview link.
"""

from .base import LLMProvider, RoleError, extract_json

SYSTEM = """You are the Builder in a preregistered paper-reproduction pipeline.
You receive only a validated-knowledge brief: which claims survived execution and
which did not, with observed values. You never see the paper. Build a thin
demonstration: exactly one HTTP API endpoint plus one static HTML page that
presents the surviving knowledge honestly (including what failed to reproduce).
Output strictly one JSON object:
- "files": map of relative path -> content; must include "app.py" (a Python http
  server on port 8000 using only the standard library) and "index.html"
- "start_command": shell command to launch the server
- "notes": one paragraph on what the page shows
Represent uncertainty faithfully: verdicts other than REPRODUCED WITHIN TOLERANCE
must be visibly distinguished, never hidden."""


def build_brief(rows: list[dict], hermeticity: str, paper_title: str) -> str:
    lines = [f"Validated-knowledge brief for: {paper_title}", f"Hermeticity: {hermeticity}", ""]
    for r in rows:
        lines.append(f"- {r['experiment_id']} claim={r['claim_id']} type={r['type']} "
                     f"observed={r['observed']} delta={r['delta']} verdict={r['verdict']}")
    return "\n".join(lines)


def propose(provider: LLMProvider, brief: str) -> dict:
    proposal = extract_json(provider.complete(SYSTEM, brief, max_tokens=8192))
    files = proposal.get("files")
    if not isinstance(files, dict) or "app.py" not in files or "index.html" not in files:
        raise RoleError("builder must return files including app.py and index.html")
    if not proposal.get("start_command"):
        raise RoleError("builder must return a start_command")
    return proposal

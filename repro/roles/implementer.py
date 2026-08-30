"""Implementer: proposes environment recipes and candidate code, executes nothing.

Sees the paper's method spec and discrepancy feedback only — never the raw metric
values, never the verifier's reasoning. Feedback arrives as (claim id, direction,
magnitude bucket), computed deterministically by the orchestrator, so the
implementer cannot fit to the target number. Proposals are (commands, files); the
orchestrator replays them through the archaeology session, which appends everything
to RECIPE.sh.
"""

from .base import LLMProvider, RoleError, extract_json

SYSTEM = """You are the Implementer in a preregistered paper-reproduction pipeline.
You propose shell commands and source files to build the training environment and
candidate implementation inside a Linux sandbox (user 'daytona', workdir
/home/daytona/work, staged datasets under localdata/). You never execute anything
yourself and never see metric targets. Output strictly one JSON object:
- "commands": ordered list of shell commands (each idempotent where possible)
- "files": map of relative path -> full file content
- "notes": short rationale
Environment mechanics only; method choices must come from the paper text you were
given. If the paper underdetermines a choice, pick the most conventional option and
name it in notes so it can be logged as an ambiguity."""

MAX_ITERATIONS = 4

BUCKETS = ((0.0, "none"), (1.0, "small"), (3.0, "moderate"), (float("inf"), "large"))


def discrepancy_feedback(claim_id: str, delta: float, tolerance: float) -> dict:
    """Deterministic feedback: direction plus magnitude bucket, no raw values."""
    magnitude = abs(delta) / tolerance if tolerance else float("inf")
    bucket = next(label for bound, label in BUCKETS if magnitude <= bound)
    return {
        "claim_id": claim_id,
        "direction": "over" if delta > 0 else ("under" if delta < 0 else "match"),
        "magnitude_bucket": bucket,
    }


def propose(provider: LLMProvider, method_spec: str, feedback: list[dict] | None = None,
            error_context: str | None = None) -> dict:
    parts = [f"Method specification from the paper:\n{method_spec[:100000]}"]
    if feedback:
        parts.append(f"Discrepancy feedback from prior attempts: {feedback}")
    if error_context:
        parts.append(f"Most recent build/run error (fix the environment, not the science):\n{error_context[-4000:]}")
    proposal = extract_json(provider.complete(SYSTEM, "\n\n".join(parts), max_tokens=8192))
    if not isinstance(proposal.get("commands"), list) or not isinstance(proposal.get("files"), dict):
        raise RoleError("implementer proposal must contain commands list and files map")
    return proposal


def apply_proposal(session, proposal: dict) -> None:
    """Replay an implementer proposal through the archaeology session (recorded)."""
    for path, content in proposal["files"].items():
        session.put_file(path, content)
    for cmd in proposal["commands"]:
        session.sh(cmd)

"""Implementer -> archaeology loop: propose, apply, smoke, feed back, retry.

The Implementer never sees a metric value. When the smoke gate fails it receives
the failing command's output; when a round completes but the gate is unhappy it
receives a discrepancy packet computed deterministically by the orchestrator
({claim_id, direction, magnitude_bucket}), exactly as the architecture specifies.
"""

import json

from ..pipeline.p1_archaeology import ArchaeologyError
from ..roles import implementer
from ..roles.base import RoleError
from .contract import implementer_system, validate_proposal

MAX_ITERATIONS = 4


def _redact(text: str, secrets: list[str]) -> str:
    """Never let a key reach the ledger or the console."""
    out = text
    for s in secrets:
        if s:
            out = out.replace(s, "***REDACTED***")
    return out


def propose(provider, method_spec: str, feedback: list[dict]) -> dict:
    """One Implementer call under the augmented system prompt, validated on return."""
    system = implementer_system(implementer.SYSTEM)
    user = f"Method spec from the paper:\n{method_spec[:120000]}"
    if feedback:
        user += ("\n\nThe previous attempt did not pass. Structured feedback "
                 f"(no metric values by design):\n{json.dumps(feedback, indent=2)}")
    from ..roles.base import extract_json
    proposal = extract_json(provider.complete(system, user, max_tokens=8192))
    return validate_proposal(proposal)


def build_to_smoke(session, provider, method_spec: str, ledger, run_id: str,
                   secrets: list[str], log=print) -> dict:
    """Drive the archaeology session to a smoke-passing state, or exhaust the cap.

    Returns {"ok": bool, "iterations": int, "attempts": [...], "last_feedback": ...}.
    """
    feedback: list[dict] = []
    attempts = []
    for i in range(1, MAX_ITERATIONS + 1):
        attempt = {"iteration": i}
        try:
            proposal = propose(provider, method_spec, feedback)
        except RoleError as e:
            attempt.update(stage="propose", error=_redact(str(e), secrets)[:600])
            attempts.append(attempt)
            ledger.log_event(run_id, "implementer_rejected", attempt)
            log(f"  round {i}: proposal rejected - {attempt['error'][:160]}")
            feedback = [{"claim_id": "*", "direction": "match",
                         "magnitude_bucket": "contract_violation",
                         "detail": attempt["error"][:400]}]
            continue

        attempt["files"] = sorted(proposal["files"])
        attempt["commands"] = len(proposal["commands"])
        log(f"  round {i}: {len(proposal['files'])} files, "
            f"{len(proposal['commands'])} commands -> applying")
        try:
            implementer.apply_proposal(session, proposal)
            session.smoke()
        except (ArchaeologyError, Exception) as e:  # noqa: BLE001 - any build failure retries
            detail = _redact(str(e), secrets)[:1200]
            attempt.update(stage="apply_or_smoke", error=detail)
            attempts.append(attempt)
            ledger.log_event(run_id, "implementer_round_failed",
                             {"iteration": i, "error": detail[:800]})
            log(f"  round {i}: smoke gate failed - {detail.splitlines()[0][:160]}")
            feedback = [{"claim_id": "*", "direction": "match",
                         "magnitude_bucket": "smoke_failure", "detail": detail[-800:]}]
            continue

        attempt["stage"] = "smoke_passed"
        attempts.append(attempt)
        ledger.log_event(run_id, "implementer_round_passed", {"iteration": i,
                                                              "files": attempt["files"]})
        log(f"  round {i}: smoke gate PASSED")
        return {"ok": True, "iterations": i, "attempts": attempts, "last_feedback": None}

    return {"ok": False, "iterations": MAX_ITERATIONS, "attempts": attempts,
            "last_feedback": feedback}

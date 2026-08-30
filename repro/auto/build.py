"""Implementer -> archaeology loop: propose, apply, smoke, feed back, retry.

The Implementer never sees a metric value. When the smoke gate fails it receives
the failing command's output; when a round completes but the gate is unhappy it
receives a discrepancy packet computed deterministically by the orchestrator
({claim_id, direction, magnitude_bucket}), exactly as the architecture specifies.
"""

import json
from pathlib import Path

from ..pipeline.p1_archaeology import ArchaeologyError
from ..roles import implementer
from ..roles.base import RoleError
from .contract import SYNTHETIC_FALLBACK, implementer_system, validate_proposal

MAX_ITERATIONS = 4

# a build round can only fail in a way the Implementer can act on if the sandbox
# is still there; when the box itself is gone the round says nothing about the
# proposal, and spending the remaining budget on it would record a build failure
# for something that never got to build
INFRA_MARKERS = ("not found: sandbox", "sandbox not found", "sandbox has been destroyed",
                 "sandbox is not running", "sandbox is in state destroyed")


class EnvironmentLost(RuntimeError):
    """The archaeology sandbox went away underneath the build loop."""


def is_environment_lost(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in INFRA_MARKERS)


def _redact(text: str, secrets: list[str]) -> str:
    """Never let a key reach the ledger or the console."""
    out = text
    for s in secrets:
        if s:
            out = out.replace(s, "***REDACTED***")
    return out


def claim_spec(claims: list[dict]) -> str:
    """The claim ids and their settings, with every reported value stripped.

    The Implementer must key config.json by the preregistered ids, so it has to
    see them; it must not see what number it is aiming at, so reported_value and
    any grading rule stay out.
    """
    visible = [{k: v for k, v in c.items()
                if k in ("id", "metric", "condition", "model", "params")}
               for c in claims]
    return json.dumps(visible, indent=2)


def propose(provider, method_spec: str, feedback: list[dict],
            claims: list[dict] | None = None, degraded: bool = False) -> dict:
    """One Implementer call under the augmented system prompt, validated on return."""
    system = implementer_system(implementer.SYSTEM)
    if degraded:
        system += SYNTHETIC_FALLBACK
    user = f"Method spec from the paper:\n{method_spec[:120000]}"
    if claims:
        user += ("\n\nconfig.json MUST be keyed by exactly these claim ids "
                 f"(no reported values are shown, by design):\n{claim_spec(claims)}")
    if feedback:
        user += ("\n\nThe previous attempt did not pass. Structured feedback "
                 f"(no metric values by design):\n{json.dumps(feedback, indent=2)}")
    from ..roles.base import extract_json
    proposal = extract_json(provider.complete(system, user, max_tokens=8192))
    return validate_proposal(proposal)


def build_to_smoke(session, provider, method_spec: str, ledger, run_id: str,
                   secrets: list[str], claims: list[dict] | None = None,
                   candidate_dir=None, parallel=None, allow_degraded: bool = True,
                   log=print) -> dict:
    """Drive the archaeology session to a smoke-passing state, or exhaust the cap.

    Returns {"ok": bool, "iterations": int, "attempts": [...], "last_feedback": ...}.
    """
    feedback: list[dict] = []
    attempts = []
    last = MAX_ITERATIONS + (1 if allow_degraded else 0)
    for i in range(1, last + 1):
        if i > MAX_ITERATIONS:
            log("  rounds exhausted; one degraded round on synthetic data")
        attempt = {"iteration": i}
        try:
            proposal = propose(provider, method_spec, feedback, claims,
                               degraded=(i > MAX_ITERATIONS))
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
        if candidate_dir is not None:
            # the source only ever existed in the sandbox's RECIPE.sh, which dies
            # with the archaeology box; keep a local copy of what was proposed
            out = Path(candidate_dir) / f"round{i}"
            out.mkdir(parents=True, exist_ok=True)
            for name, content in proposal["files"].items():
                (out / Path(name).name).write_text(content)
            (out / "commands.sh").write_text("\n".join(proposal["commands"]) + "\n")
        log(f"  round {i}: {len(proposal['files'])} files, "
            f"{len(proposal['commands'])} commands -> applying")
        try:
            implementer.apply_proposal(session, proposal, parallel=parallel)
            session.smoke()
        except (ArchaeologyError, Exception) as e:  # noqa: BLE001 - any build failure retries
            detail = _redact(str(e), secrets)[:1200]
            if is_environment_lost(detail):
                attempt.update(stage="environment_lost", error=detail)
                attempts.append(attempt)
                ledger.log_event(run_id, "environment_lost",
                                 {"iteration": i, "error": detail[:800]})
                log(f"  round {i}: ABORT - the sandbox is gone, not a build failure: "
                    f"{detail.splitlines()[0][:160]}")
                return {"ok": False, "iterations": i, "attempts": attempts,
                        "degraded": False, "environment_lost": True,
                        "last_feedback": feedback}
            attempt.update(stage="apply_or_smoke", error=detail)
            attempts.append(attempt)
            ledger.log_event(run_id, "implementer_round_failed",
                             {"iteration": i, "error": detail[:800]})
            log(f"  round {i}: smoke gate failed - {detail.splitlines()[0][:160]}")
            feedback = [{"claim_id": "*", "direction": "match",
                         "magnitude_bucket": "smoke_failure", "detail": detail[-800:]}]
            continue

        attempt["stage"] = "smoke_passed"
        attempt["degraded"] = i > MAX_ITERATIONS
        attempts.append(attempt)
        ledger.log_event(run_id, "implementer_round_passed", {"iteration": i,
                                                              "files": attempt["files"]})
        log(f"  round {i}: smoke gate PASSED")
        return {"ok": True, "iterations": i, "attempts": attempts,
                "degraded": i > MAX_ITERATIONS, "last_feedback": None}

    return {"ok": False, "iterations": last, "attempts": attempts,
            "degraded": False, "last_feedback": feedback}

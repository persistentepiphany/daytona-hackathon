"""P3 verdicts: deterministic comparison of evidence to the frozen preregistration.

Each verdict cites its claim, prereg rule id, attempt ids, and evidence hash. The
vocabulary is fixed. Held-out claims (from the orchestrator-side annex) are scored
here for the first time. Controls (ablation, randomized) produce CONTROL PASS/FAIL
rows rather than claim verdicts.
"""

import json
from pathlib import Path

from ..orchestrator.ledger import Ledger

REPRODUCED = "REPRODUCED WITHIN TOLERANCE"
OUTSIDE = "REPRODUCED OUTSIDE PREREGISTERED TOLERANCE"
NOT_REPRODUCED = "NOT REPRODUCED"
UNDER_CONSTRAINED = "UNDER-CONSTRAINED"
NOT_ATTEMPTABLE = "NOT ATTEMPTABLE"
INCONCLUSIVE = "INCONCLUSIVE"
CONTROL_PASS = "CONTROL PASS"
CONTROL_FAIL = "CONTROL FAIL"

# outside tolerance but within this multiple of it still counts as an attempt that
# tracked the claim; beyond it the claim simply did not reproduce
OUTSIDE_BAND = 3.0


def judge_experiment(entry: dict, metrics: dict | None,
                     reference_metrics: dict | None = None) -> dict:
    """entry: the preregistered experiment (with its rule). Returns a verdict row."""
    rule = entry["rule"]
    if metrics is None:
        return _row(entry, None, None, NOT_ATTEMPTABLE)
    observed = metrics.get("mean_value")
    if observed is None:
        return _row(entry, None, None, INCONCLUSIVE)

    if rule["kind"] == "abs_tolerance":
        delta = observed - rule["target"]
        if entry["type"] == "randomized_control":
            verdict = CONTROL_PASS if abs(delta) <= rule["tolerance"] else CONTROL_FAIL
        elif abs(delta) <= rule["tolerance"]:
            verdict = REPRODUCED
        elif abs(delta) <= rule["tolerance"] * OUTSIDE_BAND:
            verdict = OUTSIDE
        else:
            verdict = NOT_REPRODUCED
        return _row(entry, observed, delta, verdict)

    if rule["kind"] == "direction":
        if reference_metrics is None:
            return _row(entry, observed, None, INCONCLUSIVE)
        delta = observed - reference_metrics["mean_value"]
        if rule["direction"] == "decrease":
            ok = delta <= -rule["min_delta"]
        else:
            ok = delta >= rule["min_delta"]
        return _row(entry, observed, delta, CONTROL_PASS if ok else CONTROL_FAIL)

    return _row(entry, observed, None, UNDER_CONSTRAINED)


def _row(entry: dict, observed: float | None, delta: float | None, verdict: str) -> dict:
    return {
        "experiment_id": entry["experiment_id"],
        "claim_id": entry["claim_id"],
        "rule_id": entry["rule"]["id"],
        "type": entry["type"],
        "observed": observed,
        "delta": None if delta is None else round(delta, 6),
        "verdict": verdict,
    }


def judge_run(prereg: dict, annex: dict, evidence_root: str | Path,
              ledger: Ledger, run_id: str) -> list[dict]:
    """Score every preregistered experiment from its evidence directory."""
    evidence_root = Path(evidence_root)
    all_entries = list(prereg["experiments"]) + list(annex.get("experiments", []))
    held_ids = {c["id"] for c in annex.get("claims", [])}
    metrics_by_exp: dict[str, dict | None] = {}
    for entry in all_entries:
        path = evidence_root / entry["experiment_id"] / "metrics.json"
        metrics_by_exp[entry["experiment_id"]] = (
            json.loads(path.read_text()) if path.exists() else None
        )
    rows = []
    for entry in all_entries:
        ref = None
        if entry["rule"].get("reference_experiment"):
            ref = metrics_by_exp.get(entry["rule"]["reference_experiment"])
        row = judge_experiment(entry, metrics_by_exp[entry["experiment_id"]], ref)
        row["held_out"] = entry["claim_id"] in held_ids
        attempts = [a["attempt_id"] for a in ledger.attempts_for(run_id, entry["experiment_id"])]
        row["attempt_ids"] = attempts
        ledger.record_verdict(run_id, row["claim_id"], row["rule_id"],
                              None if row["observed"] is None else str(row["observed"]),
                              None if row["delta"] is None else str(row["delta"]),
                              row["verdict"], attempts)
        rows.append(row)
    return rows

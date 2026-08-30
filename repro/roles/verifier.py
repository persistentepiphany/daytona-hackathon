"""Verifier: sealed. Sees the preregistration, manifests, and evidence files only.

Never implementation source, agent scratchpads, or iteration history. Runs on a
different model than the Implementer. The deterministic verdict engine remains the
ground truth; the Verifier independently re-derives verdicts from the same evidence
and flags any disagreement — a disagreement is itself a finding.
"""

import json
from pathlib import Path

from .base import LLMProvider, RoleError, extract_json

SYSTEM = """You are the Verifier in a preregistered paper-reproduction pipeline.
You receive a frozen preregistration and per-experiment evidence files (manifests
and metrics). You see no implementation code and no history. For each experiment,
compare the evidence to the preregistered rule and return strictly one JSON object:
{"verdicts": [{"experiment_id", "claim_id", "rule_id", "observed", "verdict",
"citations": ["evidence file: field"]}], "concerns": ["..."]}
The verdict vocabulary, verbatim: "REPRODUCED WITHIN TOLERANCE",
"REPRODUCED OUTSIDE PREREGISTERED TOLERANCE", "NOT REPRODUCED", "UNDER-CONSTRAINED",
"NOT ATTEMPTABLE", "INCONCLUSIVE", "CONTROL PASS", "CONTROL FAIL".
Judge only from the numbers in the evidence against the rules in the
preregistration. Failure to reproduce is evidence the paper as written is
insufficient to reconstruct the result - not evidence the authors are wrong."""


def sealed_evidence_bundle(prereg: dict, evidence_root: str | Path) -> str:
    """Exactly what the verifier may see: prereg + manifests + metrics + checksums."""
    bundle = {"prereg": prereg, "evidence": {}}
    root = Path(evidence_root)
    for exp_dir in sorted(root.iterdir()) if root.exists() else []:
        if not exp_dir.is_dir():
            continue
        entry = {}
        for name in ("manifest.json", "metrics.json", "leakage.json", "checksums.json"):
            path = exp_dir / name
            if path.exists():
                entry[name] = json.loads(path.read_text())
        bundle["evidence"][exp_dir.name] = entry
    return json.dumps(bundle, indent=1, sort_keys=True)


def verify(provider: LLMProvider, prereg: dict, evidence_root: str | Path) -> dict:
    bundle = sealed_evidence_bundle(prereg, evidence_root)
    result = extract_json(provider.complete(SYSTEM, bundle, max_tokens=8192))
    if not isinstance(result.get("verdicts"), list):
        raise RoleError("verifier must return a verdicts list")
    return result


def cross_check(llm_verdicts: dict, deterministic_rows: list[dict]) -> list[str]:
    """Disagreements between the sealed verifier and the deterministic engine."""
    det = {r["experiment_id"]: r["verdict"] for r in deterministic_rows}
    disagreements = []
    for v in llm_verdicts.get("verdicts", []):
        exp = v.get("experiment_id")
        if exp in det and v.get("verdict") != det[exp]:
            disagreements.append(f"{exp}: verifier says {v.get('verdict')!r}, "
                                 f"engine says {det[exp]!r}")
    return disagreements

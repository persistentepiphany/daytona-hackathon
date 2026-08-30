"""P4: at most one adaptive round, from the same menu, under a new prereg document.

An unexpected result may motivate follow-ups, but they never modify prereg-001:
they form prereg-002, require explicit user approval (the P4 gate), and their rows
are labeled ADAPTIVE in the report. Primary verdicts are immutable once recorded.
"""

import hashlib

from ..orchestrator.ledger import Ledger
from ..orchestrator.prereg import EXPERIMENT_MENU, canonical_json


MUTATION_REQUIRED = ("ablation", "stronger_baseline", "randomized_control")


class AdaptiveError(RuntimeError):
    pass


def build_adaptive_prereg(base_prereg: dict, followups: list[dict],
                          ledger: Ledger, run_id: str) -> tuple[dict, str]:
    """followups: experiment entries (menu types only) targeting existing claims."""
    if ledger.events_for(run_id, "adaptive_prereg"):
        raise AdaptiveError("the single adaptive round was already used for this run")
    if not followups:
        raise AdaptiveError("no follow-up experiments proposed")
    known_claims = {c["id"] for c in base_prereg["claims"]}
    used_ids = {e["experiment_id"] for e in base_prereg["experiments"]}
    for f in followups:
        if f.get("type") not in EXPERIMENT_MENU:
            raise AdaptiveError(f"follow-up type outside the fixed menu: {f.get('type')}")
        if f.get("claim_id") not in known_claims:
            raise AdaptiveError(f"follow-up targets unknown claim {f.get('claim_id')}")
        if f.get("experiment_id") in used_ids:
            raise AdaptiveError(f"experiment id {f.get('experiment_id')} already used")
        if f["type"] in MUTATION_REQUIRED and not (f.get("mutation") or {}).get("config_key"):
            raise AdaptiveError(f"{f['experiment_id']}: mutation must be a config diff")
        if "rule" not in f:
            raise AdaptiveError(f"{f['experiment_id']}: missing decision rule")
    doc = {
        "version": 2,
        "role": "adaptive_round",
        "parent_prereg": None,  # filled below with the parent hash
        "paper": base_prereg["paper"],
        "claims": base_prereg["claims"],
        "experiments": followups,
        "tolerances": base_prereg["tolerances"],
        "seeds": base_prereg["seeds"],
    }
    parent_hash = hashlib.sha256(canonical_json(base_prereg).encode()).hexdigest()
    doc["parent_prereg"] = parent_hash
    text = canonical_json(doc)
    doc_hash = hashlib.sha256(text.encode()).hexdigest()
    ledger.log_event(run_id, "adaptive_prereg", {
        "hash": doc_hash, "parent": parent_hash,
        "experiments": [f["experiment_id"] for f in followups],
    })
    return doc, doc_hash

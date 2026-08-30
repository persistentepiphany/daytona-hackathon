"""Planner: reads the paper, proposes the contract. Sees the paper only.

Output is validated deterministically before it can reach G1: every claim needs the
required fields, every proposed experiment must come from the fixed menu, every
ambiguity must map to a config key. Anything else is rejected back to the Planner —
the orchestrator never repairs proposals silently.
"""

from ..orchestrator.prereg import EXPERIMENT_MENU
from .base import LLMProvider, RoleError, extract_json

SYSTEM = f"""You are the Planner in a preregistered paper-reproduction pipeline.
You read a paper and propose an executable contract. You never see any code and
never run anything. Output strictly one JSON object with keys:
- "claims": list of {{"id", "metric", "dataset", "split", "reported_value", "model", "params", "source_loc"}}
- "ambiguities": list of {{"id", "question", "config_key"}} — decisions the paper
  leaves underdetermined, each mapped to a config key. Do not resolve them from
  outside sources; unresolvable gaps become UNDER-CONSTRAINED findings.
- "experiments": list of {{"experiment_id", "claim_id", "type", "rule", "mutation"?}}
  where type is one of {list(EXPERIMENT_MENU)} and rule is
  {{"id", "kind": "abs_tolerance"|"direction", "target"?, "tolerance"?, "aggregate": "mean"}}.
  Mutations must be config diffs: {{"config_key", "value"}}.
- "tolerances": map claim_id -> absolute tolerance, justified by the paper's own
  variance reporting where present.
- "cost_estimate": {{"sandbox_hours", "notes"}}
Pick claims that are executable on CPU within the stated budget. Be conservative:
fewer, better-grounded claims beat coverage."""

REQUIRED_CLAIM_FIELDS = ("id", "metric", "dataset", "split", "reported_value", "source_loc")


def propose(provider: LLMProvider, paper_text: str, objective: str, depth: str) -> dict:
    user = (f"Objective: {objective}\nDepth: {depth}\n\nPaper text:\n{paper_text[:150000]}")
    proposal = extract_json(provider.complete(SYSTEM, user, max_tokens=8192))
    validate_proposal(proposal)
    return proposal


def validate_proposal(p: dict) -> None:
    claims = p.get("claims") or []
    if not claims:
        raise RoleError("planner proposed no claims")
    ids = set()
    for c in claims:
        for f in REQUIRED_CLAIM_FIELDS:
            if f not in c:
                raise RoleError(f"claim missing {f}: {c}")
        if c["id"] in ids:
            raise RoleError(f"duplicate claim id {c['id']}")
        ids.add(c["id"])
    for e in p.get("experiments") or []:
        if e.get("type") not in EXPERIMENT_MENU:
            raise RoleError(f"experiment type outside the fixed menu: {e.get('type')}")
        if e.get("claim_id") not in ids:
            raise RoleError(f"experiment targets unknown claim {e.get('claim_id')}")
        if e["type"] != "reproduce" and not e.get("mutation", {}).get("config_key"):
            raise RoleError(f"{e.get('experiment_id')}: non-reproduce experiments need a config-diff mutation")
    for a in p.get("ambiguities") or []:
        if not a.get("config_key"):
            raise RoleError(f"ambiguity without config key: {a}")
    for cid in (p.get("tolerances") or {}):
        if cid not in ids:
            raise RoleError(f"tolerance for unknown claim {cid}")

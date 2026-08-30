"""Planner: reads the paper, proposes the contract. Sees the paper only.

Output is validated deterministically before it can reach G1: every claim needs the
required fields, every proposed experiment must come from the fixed menu, every
ambiguity must map to a config key. Anything else is rejected back to the Planner —
the orchestrator never repairs proposals silently.
"""

from ..orchestrator.prereg import EXPERIMENT_MENU
from ..orchestrator.schemas import SchemaError, normalize_claim, validate_ambiguity
from .base import LLMProvider, RoleError, extract_json

SYSTEM = f"""You are the Planner in a preregistered paper-reproduction pipeline.
You read a paper and propose an executable contract. You never see any code and
never run anything. Output strictly one JSON object with keys:
- "claims": list of {{"id", "metric", "condition", "reported_value", "model", "params", "source_loc"}}
  where "condition" is the experimental setting as an object (e.g.
  {{"dataset": ..., "split": ...}} or {{"n": 100, "contamination": 0.1,
  "distribution": "normal", "replications": 1000}}).
- "ambiguities": list of {{"id", "question", "config_key", "type"}} — decisions the
  paper leaves underdetermined, each mapped to a config key, typed as one of
  "unstated_choice", "equation_ambiguity", "version_dependent_default". Do not
  resolve them from outside sources; unresolvable gaps become UNDER-CONSTRAINED
  findings.
- "experiments": list of {{"experiment_id", "claim_id", "type", "rule", "mutation"?}}
  where type is one of {list(EXPERIMENT_MENU)} and rule is
  {{"id", "kind": "abs_tolerance"|"direction", "target"?, "tolerance"?, "aggregate": "mean"}}.
  Mutations must be config diffs: {{"config_key", "value"}}.
- "tolerances": map claim_id -> absolute tolerance, justified by the paper's own
  variance reporting where present.
- "data_requirements": list of paper-declared datasets as
  {{"id", "url", "filename", "sha256"?, "license"?, "required": true}}.
  Include a URL only when it is present in the paper. Never invent a download URL
  or checksum; an unresolved required dataset is an UNDER-CONSTRAINED finding.
- "cost_estimate": {{"sandbox_hours", "notes"}}
Pick claims that are executable on CPU within the stated budget. Be conservative:
fewer, better-grounded claims beat coverage."""

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
    for i, c in enumerate(claims):
        try:
            claims[i] = c = normalize_claim(c)
        except SchemaError as e:
            raise RoleError(str(e)) from e
        if c["id"] in ids:
            raise RoleError(f"duplicate claim id {c['id']}")
        ids.add(c["id"])
    for e in p.get("experiments") or []:
        if e.get("type") not in EXPERIMENT_MENU:
            raise RoleError(f"experiment type outside the fixed menu: {e.get('type')}")
        if e.get("claim_id") not in ids:
            raise RoleError(f"experiment targets unknown claim {e.get('claim_id')}")
        if (e["type"] in ("ablation", "stronger_baseline", "randomized_control")
                and not e.get("mutation", {}).get("config_key")):
            raise RoleError(f"{e.get('experiment_id')}: {e['type']} experiments need a config-diff mutation")
    ambiguities = p.get("ambiguities") or []
    for i, a in enumerate(ambiguities):
        try:
            ambiguities[i] = validate_ambiguity(a)
        except SchemaError as e:
            raise RoleError(str(e)) from e
    for cid in (p.get("tolerances") or {}):
        if cid not in ids:
            raise RoleError(f"tolerance for unknown claim {cid}")
    for dataset in p.get("data_requirements") or []:
        if not isinstance(dataset, dict) or not dataset.get("id"):
            raise RoleError("each data requirement needs an id")
        url = dataset.get("url")
        if url is not None and not isinstance(url, str):
            raise RoleError(f"dataset {dataset['id']}: url must be a string")
        if dataset.get("required", True) and not url:
            dataset["unresolved"] = True
    estimate = p.get("cost_estimate") or {}
    hours = estimate.get("sandbox_hours", 0)
    if not isinstance(hours, (int, float)) or hours < 0:
        raise RoleError("cost_estimate.sandbox_hours must be a non-negative number")
    if hours > 25:
        raise RoleError("proposed cost exceeds the 1,500 sandbox-minute policy ceiling")

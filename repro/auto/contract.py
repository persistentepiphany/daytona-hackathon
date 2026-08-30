"""Planner proposal -> prereg_inputs, and the Implementer's executable contract.

`prereg_inputs()` returns exactly what `cal.prereg_inputs()` returns, so the
autonomous path hands `build_prereg` the same five values the calibration path
does. The Implementer contract is enforced before a sandbox is spent: a proposal
that cannot possibly satisfy `RUNNER_PY` is rejected on the spot.
"""

from ..orchestrator.prereg import EXPERIMENT_MENU
from ..roles.base import RoleError

# RUNNER_PY invokes `venv/bin/python train.py --claim <id> --seed <n> [--set k=v]`
# and parses the last stdout line as the metrics object. Anything that does not
# meet that contract fails inside the experiment sandbox, long after the spend.
CONTRACT = """
MANDATORY OUTPUT CONTRACT - a proposal that omits any of this is rejected unrun:

0. Every value in "files" MUST be a STRING holding the file's full text. For
   config.json that means a JSON-encoded string, never a nested JSON object.
1. "files" MUST include "train.py". It is invoked as
     venv/bin/python train.py --claim <claim_id> --seed <int> [--set key=value ...]
   It MUST accept repeated --set overrides of dotted config keys (at minimum
   data.dir) and MUST print, as its LAST stdout line, exactly one JSON object:
     {"claim": "<claim_id>", "seed": <int>, "metric": "<name>", "value": <float>,
      "train_seconds": <float>, "n_train": <int>, "n_test": <int>,
      "config_overrides": [<the --set strings>]}
   No other text on that final line.
2. "files" MUST include "config.json" with a "data" object carrying a "dir" key,
   plus one entry per claim, keyed by EXACTLY the claim ids given to you in the
   user message. Do not invent your own claim ids: the runner invokes
   train.py --claim <that id> and anything else fails unrun.
3. "files" MUST include "dataio.py" exposing load_split(data_dir, split) ->
   (X, y) as numpy arrays, for both split="train" and split="test". A ride-along
   integrity check imports it; train.py should use it too.
4. "files" MUST include "smoke.sh": a fast end-to-end check (seconds, not
   minutes) that exercises the real code path and exits non-zero on failure.
5. Build a virtualenv at ./venv and install dependencies into it, so
   venv/bin/python exists. Pin nothing you do not need.
6. Any dataset the paper requires must be acquired by your own commands during
   this build and left on local disk under ./localdata, because experiment
   sandboxes run with networking disabled. Set config.json's data.dir to
   "localdata".
"""

REQUIRED_FILES = ("train.py", "config.json", "dataio.py", "smoke.sh")


def implementer_system(base_system: str) -> str:
    """Append the executable contract to the Implementer's own system prompt."""
    return base_system + "\n" + CONTRACT


def validate_proposal(proposal: dict) -> dict:
    """Reject a proposal that cannot satisfy the runner, before spending a sandbox."""
    files = proposal.get("files")
    if not isinstance(files, dict) or not files:
        raise RoleError("implementer proposal carries no files")
    missing = [f for f in REQUIRED_FILES if f not in files]
    if missing:
        raise RoleError(f"implementer proposal missing required file(s): {missing}")
    nonstring = sorted(k for k, v in files.items() if not isinstance(v, str))
    if nonstring:
        raise RoleError(f"file contents must be strings, not objects: {nonstring}; "
                        f"JSON-encode config.json as a string")
    train = files["train.py"]
    for token in ("--claim", "--seed", "--set"):
        if token not in train:
            raise RoleError(f"train.py does not handle {token}")
    if not isinstance(proposal.get("commands"), list) or not proposal["commands"]:
        raise RoleError("implementer proposal carries no commands")
    return proposal


def prereg_inputs(paper: dict, proposal: dict, seeds: list[int],
                  max_claims: int = 2) -> tuple[dict, list[dict], list[dict],
                                                dict[str, float], list[int]]:
    """Convert a validated Planner proposal into `cal.prereg_inputs()` shape.

    The Planner already emits claims, experiments carrying grading rules, and a
    tolerance map; `planner.validate_proposal` has normalized the claims. What is
    left is selecting a runnable subset, keeping experiments and tolerances
    consistent with it, and defaulting anything the prereg builder requires.
    """
    claims = [c for c in proposal["claims"] if c.get("reported_value") is not None]
    if not claims:
        raise RoleError("planner proposed no claim with a reported value")
    claims = claims[:max_claims]
    keep = {c["id"] for c in claims}

    experiments = []
    for e in proposal.get("experiments", []):
        if e.get("claim_id") not in keep or e.get("type") not in EXPERIMENT_MENU:
            continue
        if e.get("type") != "reproduce" or not e.get("rule"):
            continue
        entry = dict(e)
        entry.setdefault("command", f"bash runner.sh {entry['experiment_id']}")
        claim = next(c for c in claims if c["id"] == entry["claim_id"])
        entry.setdefault("condition", claim.get("condition"))
        # p3_verdict does observed - rule["target"]; a planner rule that omits it
        # would crash the grader after the compute has already been paid for
        rule = dict(entry["rule"])
        rule.setdefault("target", claim["reported_value"])
        rule.setdefault("aggregate", "mean")
        entry["rule"] = rule
        experiments.append(entry)
    if not experiments:
        raise RoleError("planner proposed no reproduce experiment for the kept claims")
    # one experiment per claim keeps the run cheap and the table legible
    seen, deduped = set(), []
    for e in experiments:
        if e["claim_id"] in seen:
            continue
        seen.add(e["claim_id"])
        deduped.append(e)
    keep = {e["claim_id"] for e in deduped}
    claims = [c for c in claims if c["id"] in keep]

    tolerances = {}
    for c in claims:
        tol = (proposal.get("tolerances") or {}).get(c["id"])
        if tol is None:
            rule = next(e["rule"] for e in deduped if e["claim_id"] == c["id"])
            tol = rule.get("tolerance")
        if tol is None:
            raise RoleError(f"no tolerance for claim {c['id']}")
        tolerances[c["id"]] = float(tol)
    return paper, claims, deduped, tolerances, seeds

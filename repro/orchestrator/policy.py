"""Run policy: budget caps, the fixed experiment menu, and Parallel gating.

One document controls spend and search behavior for a run. Loaded from a JSON
file when present, falling back to these defaults; the Parallel section feeds the
client's stage gates and the global off-switch. The pipeline must complete
end-to-end with `parallel.enabled` false.
"""

import json
from pathlib import Path

from .prereg import EXPERIMENT_MENU

DEFAULTS = {
    "budget": {"sandbox_minutes": 4000, "parallel_calls": 12, "llm_calls": 200},
    "experiment_menu": list(EXPERIMENT_MENU),
    "parallel": {
        "enabled": True,
        "enabled_stages": ["intake", "archaeology"],
        "per_stage_caps": {"intake": 3, "archaeology": 10},
    },
    "mc_tolerance_k": 3.0,
}


def load_policy(path: str | Path | None = None) -> dict:
    policy = json.loads(json.dumps(DEFAULTS))  # deep copy
    if path and Path(path).exists():
        overrides = json.loads(Path(path).read_text())
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(policy.get(key), dict):
                policy[key].update(value)
            else:
                policy[key] = value
    return policy


def parallel_stages(policy: dict) -> tuple[str, ...]:
    p = policy["parallel"]
    return tuple(p["enabled_stages"]) if p.get("enabled") else ()

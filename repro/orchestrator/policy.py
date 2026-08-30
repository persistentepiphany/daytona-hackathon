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
    # the live feed, opt-in. REPRO_TELEMETRY=1 turns it on for a run; this key is the
    # default when the variable is unset. Off means no feed events, no sandbox log tap
    # and no extra provider calls, so a run behaves exactly as it did before the feed
    # existed.
    "telemetry": {
        "enabled": False,
        "port": 8700,
        "pool_width": 2,          # concurrent experiment sandboxes; the org memory quota
        "default_attempt_seconds": 900,
    },
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


def telemetry_enabled(policy: dict | None = None) -> bool:
    """The single answer to "is the feed on?".

    The environment has the last word so a run can opt in without editing a file;
    otherwise the policy decides, defaulting to off. `telemetry.Bus` calls this rather
    than reading the variable itself, so the key here cannot drift out of use.
    """
    from ..env import env_key

    flag = env_key("REPRO_TELEMETRY")
    if flag is not None:
        return flag.strip().lower() in ("1", "true", "on", "yes")
    if policy is None:
        policy = DEFAULTS
    return bool(policy.get("telemetry", {}).get("enabled", False))

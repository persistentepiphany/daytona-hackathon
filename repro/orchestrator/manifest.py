"""Experiment manifests: frozen before execution, derivable from the prereg only.

A manifest carries no science of its own — every load-bearing field (claim, type,
mutation, seeds, command) must equal what the frozen preregistration declares for
that experiment id. The gate is deterministic: any mismatch rejects, no judgement
calls. Decision rules and tolerances live in the prereg alone, so a manifest cannot
smuggle in a different bar to clear.
"""

from .prereg import EXPERIMENT_MENU, canonical_json, sha256_of

DEFAULT_BUDGET = {"ttl_min": 90, "cpu": 2, "memory_gib": 4}


class ManifestError(RuntimeError):
    pass


def build_manifest(prereg: dict, prereg_hash: str, experiment_id: str,
                   budget: dict | None = None) -> dict:
    """Derive the manifest for one preregistered experiment. Derivation is the only
    constructor: hand-rolled manifests must survive validate_manifest unchanged."""
    entry = _find_experiment(prereg, experiment_id)
    manifest = {
        "experiment_id": experiment_id,
        "prereg_hash": prereg_hash,
        "claim_id": entry["claim_id"],
        "type": entry["type"],
        "condition": entry.get("condition"),
        "mutation": entry.get("mutation"),
        "seeds": list(entry.get("seeds") or prereg["seeds"]),
        "command": entry.get("command", f"bash runner.sh {experiment_id}"),
        "expected_outputs": ["metrics.json"],
        "budget": dict(budget or DEFAULT_BUDGET),
    }
    return manifest


def manifest_hash(manifest: dict) -> str:
    return sha256_of(manifest)


def validate_manifest(manifest: dict, prereg: dict, prereg_hash: str) -> str:
    """Deterministic gate. Returns the manifest hash; raises ManifestError on any mismatch."""
    if manifest.get("prereg_hash") != prereg_hash:
        raise ManifestError("manifest prereg_hash does not match the frozen preregistration")
    exp_id = manifest.get("experiment_id")
    entry = _find_experiment(prereg, exp_id)

    if manifest.get("claim_id") != entry["claim_id"]:
        raise ManifestError(f"{exp_id}: claim_id {manifest.get('claim_id')} not preregistered")
    if manifest.get("type") != entry["type"]:
        raise ManifestError(f"{exp_id}: type {manifest.get('type')} not preregistered")
    if manifest["type"] not in EXPERIMENT_MENU:
        raise ManifestError(f"{exp_id}: type outside the fixed menu")

    if manifest["type"] == "reproduce":
        if manifest.get("mutation") is not None:
            raise ManifestError(f"{exp_id}: reproduce experiments carry no mutation")
    else:
        if manifest.get("mutation") != entry.get("mutation"):
            raise ManifestError(f"{exp_id}: mutation differs from the preregistered config diff")

    if manifest.get("condition") != entry.get("condition"):
        raise ManifestError(f"{exp_id}: condition differs from the preregistered setting")

    expected_seeds = list(entry.get("seeds") or prereg["seeds"])
    if list(manifest.get("seeds") or []) != expected_seeds:
        raise ManifestError(f"{exp_id}: seeds not derivable from the preregistration")

    expected_cmd = entry.get("command", f"bash runner.sh {exp_id}")
    if manifest.get("command") != expected_cmd:
        raise ManifestError(f"{exp_id}: command differs from the preregistered contract")

    if "metrics.json" not in (manifest.get("expected_outputs") or []):
        raise ManifestError(f"{exp_id}: metrics.json must be an expected output")

    budget = manifest.get("budget") or {}
    if not isinstance(budget.get("ttl_min"), (int, float)) or budget["ttl_min"] <= 0:
        raise ManifestError(f"{exp_id}: budget.ttl_min must be a positive number")

    return manifest_hash(manifest)


def _find_experiment(prereg: dict, experiment_id: str) -> dict:
    for entry in prereg["experiments"]:
        if entry["experiment_id"] == experiment_id:
            return entry
    raise ManifestError(f"experiment {experiment_id} is not preregistered")


def dump_manifest(manifest: dict) -> str:
    return canonical_json(manifest)

"""Claim and ambiguity schemas.

Claims carry a generalized `condition` object — an arbitrary experimental setting
(e.g. {n, contamination, distribution, replications}); the legacy dataset/split
pair is still accepted and normalized into a condition, so nothing built on the
earlier shape breaks. Ambiguity ledger entries are typed with a fixed enum.
"""

REQUIRED_CLAIM_FIELDS = ("id", "metric", "reported_value", "source_loc")

AMBIGUITY_TYPES = ("unstated_choice", "equation_ambiguity", "version_dependent_default")


class SchemaError(ValueError):
    pass


def normalize_claim(claim: dict) -> dict:
    """Validate a claim and return it with a `condition` object guaranteed.

    Legacy {dataset, split} claims get condition={"dataset":..., "split":...};
    original keys are preserved so existing consumers keep working.
    """
    for field in REQUIRED_CLAIM_FIELDS:
        if field not in claim:
            raise SchemaError(f"claim missing {field}: {claim}")
    out = dict(claim)
    condition = out.get("condition")
    if condition is None:
        legacy = {k: out[k] for k in ("dataset", "split") if k in out}
        if not legacy:
            raise SchemaError(f"claim {out['id']} needs a condition object "
                              f"(or legacy dataset/split fields)")
        condition = legacy
        out["condition"] = condition
    if not isinstance(condition, dict) or not condition:
        raise SchemaError(f"claim {out['id']}: condition must be a non-empty object")
    return out


def validate_ambiguity(entry: dict) -> dict:
    """Entries need a config key and a type from the fixed enum (untyped legacy
    entries default to unstated_choice)."""
    if not entry.get("config_key"):
        raise SchemaError(f"ambiguity without config key: {entry}")
    out = dict(entry)
    kind = out.setdefault("type", "unstated_choice")
    if kind not in AMBIGUITY_TYPES:
        raise SchemaError(f"ambiguity type {kind!r} not in {AMBIGUITY_TYPES}")
    return out

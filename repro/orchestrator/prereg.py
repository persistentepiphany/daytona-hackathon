"""Preregistration: the frozen contract every experiment is judged against.

Built once at G1 from the claims table and the proposed experiment set, hashed,
and never modified. Held-out claim ids are stored orchestrator-side only — they
are excluded from the prereg document agents see, and scored only at P3.
"""

import hashlib
import json
import random
from pathlib import Path

EXPERIMENT_MENU = ("reproduce", "ablation", "stronger_baseline", "randomized_control", "seed_sweep")

VERDICTS = (
    "REPRODUCED WITHIN TOLERANCE",
    "REPRODUCED OUTSIDE PREREGISTERED TOLERANCE",
    "NOT REPRODUCED",
    "UNDER-CONSTRAINED",
    "NOT ATTEMPTABLE",
    "INCONCLUSIVE",
)


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_of(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


class PreregError(RuntimeError):
    pass


def build_prereg(paper: dict, claims: list[dict], experiments: list[dict],
                 tolerances: dict[str, float], seeds: list[int],
                 held_out_fraction: float = 0.2, rng_seed: int | None = None) -> tuple[dict, dict]:
    """Returns (prereg_document, held_out_annex).

    The document freezes: claims (minus held-out), experiment definitions from the
    fixed menu, per-claim tolerances and decision rules, and the seed list. The
    annex holds the held-out claims and their experiments in the same shape; it is
    stored orchestrator-side only and never shown to any agent role.
    """
    for exp in experiments:
        if exp["type"] not in EXPERIMENT_MENU:
            raise PreregError(f"experiment type {exp['type']} not in the fixed menu")
        for key in ("experiment_id", "claim_id", "rule"):
            if key not in exp:
                raise PreregError(f"experiment missing {key}: {exp}")
    claim_ids = [c["id"] for c in claims]
    for cid, tol in tolerances.items():
        if cid not in claim_ids:
            raise PreregError(f"tolerance for unknown claim {cid}")

    rng = random.Random(rng_seed)
    n_held = max(1, int(len(claims) * held_out_fraction)) if len(claims) > 2 else 0
    held_out = sorted(rng.sample(claim_ids, n_held)) if n_held else []
    # a held-out claim only moves reproduce-type experiments to the annex; ablations
    # and controls that reference a held-out claim would leak its existence, so the
    # caller should target visible claims with those
    header = {"paper_id": paper["paper_id"], "pdf_sha256": paper["pdf_sha256"]}
    doc = {
        "version": 1,
        "paper": header,
        "claims": [c for c in claims if c["id"] not in held_out],
        "experiments": [e for e in experiments if e["claim_id"] not in held_out],
        "tolerances": {cid: t for cid, t in tolerances.items() if cid not in held_out},
        "seeds": seeds,
        "menu": list(EXPERIMENT_MENU),
        "framing": ("failure to reproduce is evidence the paper as written is insufficient "
                    "to reconstruct the result - not evidence the authors are wrong"),
    }
    annex = {
        "version": 1,
        "role": "held_out_annex",
        "paper": header,
        "claims": [c for c in claims if c["id"] in held_out],
        "experiments": [e for e in experiments if e["claim_id"] in held_out],
        "tolerances": {cid: t for cid, t in tolerances.items() if cid in held_out},
        "seeds": seeds,
    }
    return doc, annex


def freeze_prereg(doc: dict, out_dir: str | Path) -> str:
    """Write prereg.json and return its sha256. The caller commits it as commit #1."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    text = canonical_json(doc)
    (out_dir / "prereg.json").write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def load_prereg(path: str | Path) -> tuple[dict, str]:
    text = Path(path).read_text()
    return json.loads(text), hashlib.sha256(text.encode()).hexdigest()

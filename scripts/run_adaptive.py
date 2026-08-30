"""P4 adaptive round for the calibration run: one follow-up from the fixed menu.

The primary run showed GaussianNB (C4) far above its reported value. The paper
leaves the pixel-scaling choice underdetermined (ambiguity A2: raw 0-255 vs
scaled), and Gaussian smoothing breaks scale invariance — so the preregistered
follow-up asks: does feeding raw pixels reproduce the reported value? Runs as
prereg-002 under the P4 gate; rows are labeled ADAPTIVE and cannot alter primary
verdicts.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repro.orchestrator.budget import Budget  # noqa: E402
from repro.orchestrator.daytona_client import DaytonaAdapter  # noqa: E402
from repro.orchestrator.gates import Gates  # noqa: E402
from repro.orchestrator.ledger import Ledger  # noqa: E402
from repro.orchestrator.lifecycle import Lifecycle  # noqa: E402
from repro.orchestrator.manifest import build_manifest  # noqa: E402
from repro.orchestrator.prereg import load_prereg  # noqa: E402
from repro.pipeline import p3_verdict as p3  # noqa: E402
from repro.pipeline.p2_experiments import run_experiment  # noqa: E402
from repro.pipeline.p4_adaptive import build_adaptive_prereg  # noqa: E402

RUN_ROOT = Path("runs/calibration")

FOLLOWUPS = [{
    "experiment_id": "A201",
    "claim_id": "C4",
    "type": "ablation",
    "command": "bash runner.sh A201",
    "mutation": {"config_key": "data.scale", "value": 1.0},
    "rule": {"id": "R-A201", "kind": "abs_tolerance", "target": 0.511,
             "tolerance": 0.01, "aggregate": "mean"},
}]


def main() -> int:
    handle = json.loads((RUN_ROOT / "latest.json").read_text())
    run_id = handle["run_id"]
    run_dir = Path(handle["run_dir"])
    prereg, _ = load_prereg(run_dir / "prereg.json")
    ledger = Ledger(handle["ledger"])
    gates = Gates(ledger)
    budget = Budget(ledger, run_id, {"sandbox_minutes": 4000})
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, budget, run_id)

    doc, doc_hash = build_adaptive_prereg(prereg, FOLLOWUPS, ledger, run_id)
    (run_dir / "prereg_002.json").write_text(json.dumps(doc, indent=2))
    if not gates.passed(run_id, "P4"):
        gates.approve(run_id, "P4", "user")
    print(f"[{run_id}] prereg-002 approved: {doc_hash[:16]}", flush=True)

    manifest = build_manifest(doc, doc_hash, "A201",
                              budget={"ttl_min": 20, "cpu": 2, "memory_gib": 4})
    m = run_experiment(life, adapter, ledger, run_id, doc, doc_hash, manifest,
                       handle["s0_snapshot"], handle["dataset_hashes"],
                       run_dir / "evidence")
    print(f"  A201 mean={m['mean_value']}", flush=True)

    rows = p3.judge_run(doc, {"claims": [], "experiments": []},
                        run_dir / "evidence", ledger, run_id)
    verdicts = json.loads((run_dir / "verdicts.json").read_text())
    verdicts["adaptive"] = rows
    (run_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=2))
    for r in rows:
        print(f"  ADAPTIVE {r['experiment_id']} observed={r['observed']} "
              f"delta={r['delta']} verdict={r['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

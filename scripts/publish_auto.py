"""Deploy the P5 preview for an autonomous run and print its URL.

scripts/auto_run.py stops at P3 so a failed run costs no build sandbox; this
takes the graded verdicts it left behind and deploys the same thin
"what survived" app the calibration path deploys.
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
from repro.pipeline.p5_build import deploy, fallback_app_files  # noqa: E402

RUN_ROOT = Path("runs/auto")
PAPER_TITLE = "Fashion-MNIST (arXiv:1708.07747) - autonomous run"


def main() -> int:
    handle = json.loads((RUN_ROOT / "latest.json").read_text())
    run_id = handle["run_id"]
    run_dir = Path(handle["run_dir"])
    verdicts = json.loads((run_dir / "verdicts.json").read_text())
    ledger = Ledger(RUN_ROOT / "ledger.db")
    gates = Gates(ledger)
    budget = Budget(ledger, run_id, {"sandbox_minutes": 1500})
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, budget, run_id)

    lineage = {"run_id": run_id, "prereg": handle["prereg_hash"],
               "s0": handle["s0_snapshot"],
               "build_iterations": handle.get("build_iterations")}
    files = fallback_app_files(verdicts["verdicts"], verdicts["hermeticity"],
                               PAPER_TITLE, lineage)
    deployment = deploy(life, adapter, ledger, run_id, files, demo_window=True)
    (run_dir / "deployment.json").write_text(json.dumps(deployment, indent=2))
    print(json.dumps(deployment, indent=2))
    print(f"\npreview: {deployment['preview_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

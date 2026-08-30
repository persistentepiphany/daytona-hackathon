"""Finish a calibration run: add the drift-stable sham row if missing, regenerate
the report with the code-absence trail, copy canonical artifacts into results/,
and deploy the thin what-survived app to a sandbox with a preview link.
"""

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from run_calibration_p2 import TTL_MIN, _sham_prereg  # noqa: E402

from repro.orchestrator.budget import Budget  # noqa: E402
from repro.orchestrator.daytona_client import DaytonaAdapter  # noqa: E402
from repro.orchestrator.gates import Gates  # noqa: E402
from repro.orchestrator.ledger import Ledger  # noqa: E402
from repro.orchestrator.lifecycle import Lifecycle  # noqa: E402
from repro.orchestrator.manifest import build_manifest  # noqa: E402
from repro.orchestrator.prereg import load_prereg  # noqa: E402
from repro.pipeline import p3_verdict as p3  # noqa: E402
from repro.pipeline.p2_experiments import run_experiment  # noqa: E402
from repro.pipeline.p5_build import deploy, fallback_app_files  # noqa: E402
from repro.pipeline.report import generate_report  # noqa: E402

RUN_ROOT = Path("runs/calibration")
RESULTS_ROOT = Path("results/calibration")
PAPER_TITLE = "Fashion-MNIST (arXiv:1708.07747)"


def main() -> int:
    handle = json.loads((RUN_ROOT / "latest.json").read_text())
    run_id = handle["run_id"]
    run_dir = Path(handle["run_dir"])
    prereg, prereg_hash = load_prereg(run_dir / "prereg.json")
    verdicts = json.loads((run_dir / "verdicts.json").read_text())
    ledger = Ledger(handle["ledger"])
    gates = Gates(ledger)
    budget = Budget(ledger, run_id, {"sandbox_minutes": 4000})
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, budget, run_id)
    evidence_root = run_dir / "evidence"

    if not any(r["experiment_id"] == "SH02" for r in verdicts["sham"]):
        print("running drift-stable sham SH02 (corrupted C1 target)", flush=True)
        sham2, sham2_hash = _sham_prereg(prereg, claim_id="C1", exp_id="SH02")
        manifest = build_manifest(sham2, sham2_hash, "SH02",
                                  budget={"ttl_min": TTL_MIN.get("E001", 30),
                                          "cpu": 2, "memory_gib": 4})
        ledger.log_event(run_id, "sham_defined", {"delta": 0.05, "hash": sham2_hash,
                                                  "claim": "C1", "exp": "SH02"})
        m = run_experiment(life, adapter, ledger, run_id, sham2, sham2_hash, manifest,
                           handle["s0_snapshot"], handle["dataset_hashes"], evidence_root)
        print(f"  SH02 mean={m['mean_value']}", flush=True)
        rows = p3.judge_run(sham2, {"claims": [], "experiments": []},
                            evidence_root, ledger, run_id)
        verdicts["sham"].extend(rows)
        (run_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=2))

    code_absence = json.loads(Path("papers/fashion-mnist/code_absence.json").read_text())
    report_text = generate_report(
        run_id, prereg, verdicts["verdicts"], verdicts["sham"],
        verdicts["hermeticity"], ledger, PAPER_TITLE, code_absence=code_absence,
    )
    (run_dir / "report.md").write_text(report_text)

    out = RESULTS_ROOT / run_id
    out.mkdir(parents=True, exist_ok=True)
    for name in ("prereg.json", "prereg_annex.json", "verdicts.json", "report.md"):
        shutil.copy2(run_dir / name, out / name)
    shutil.copy2("papers/fashion-mnist/code_absence.json", out / "code_absence.json")
    print(f"artifacts copied to {out}", flush=True)

    print("deploying what-survived app", flush=True)
    rows = verdicts["sham"] + verdicts["verdicts"] + verdicts.get("adaptive", [])
    lineage = {"run_id": run_id, "prereg": prereg_hash,
               "s0": handle["s0_snapshot"], "recipe": handle["recipe_sha"]}
    files = fallback_app_files(rows, verdicts["hermeticity"], PAPER_TITLE, lineage)
    deployment = deploy(life, adapter, ledger, run_id, files)
    (out / "deployment.json").write_text(json.dumps(deployment, indent=2))
    print(json.dumps(deployment, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

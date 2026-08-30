"""Canonical P1 run on the calibration paper, in pipeline order: build and freeze
the preregistration (G1), then stage data, build the environment, pass the smoke
gate, freeze S0, and verify a fresh boot from S0. Writes the run handle to
runs/calibration/latest.json for P2.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repro.calibration import fashion_mnist as cal  # noqa: E402
from repro.orchestrator.budget import Budget  # noqa: E402
from repro.orchestrator.daytona_client import DaytonaAdapter  # noqa: E402
from repro.orchestrator.gates import Gates  # noqa: E402
from repro.orchestrator.ledger import Ledger  # noqa: E402
from repro.orchestrator.lifecycle import Lifecycle  # noqa: E402
from repro.orchestrator.prereg import build_prereg, canonical_json, freeze_prereg  # noqa: E402
from repro.pipeline.p1_archaeology import ArchaeologySession  # noqa: E402
from repro.pipeline.staging import stage_datasets  # noqa: E402

RUN_ROOT = Path("runs/calibration")


def main() -> int:
    run_id = f"cal-{int(time.time())}"
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # G1: approve and freeze the preregistration before anything can spend
    paper, claims, experiments, tolerances, seeds = cal.prereg_inputs()
    doc, annex = build_prereg(paper, claims, experiments, tolerances, seeds, rng_seed=1337)
    prereg_hash = freeze_prereg(doc, run_dir)
    annex_text = canonical_json(annex)
    (run_dir / "prereg_annex.json").write_text(annex_text)
    annex_hash = hashlib.sha256(annex_text.encode()).hexdigest()
    held = [c["id"] for c in annex["claims"]]
    print(f"[{run_id}] prereg frozen {prereg_hash[:16]} "
          f"({len(doc['claims'])} visible claims, held-out: {held})", flush=True)

    ledger = Ledger(RUN_ROOT / "ledger.db")
    ledger.create_run(run_id, paper_hash=paper["pdf_sha256"], prereg_hash=prereg_hash)
    ledger.log_event(run_id, "prereg_frozen", {
        "prereg_hash": prereg_hash, "annex_hash": annex_hash, "held_out_count": len(held),
    })
    gates = Gates(ledger)
    gates.approve(run_id, "G1", "user")
    budget = Budget(ledger, run_id, {"sandbox_minutes": 4000, "parallel_calls": 12})
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, budget, run_id)

    print(f"[{run_id}] staging datasets", flush=True)
    volume_id = adapter.volume_ensure("datasets")
    hashes = stage_datasets(life, adapter, ledger, run_id, "daytona-small", "datasets",
                            cal.DATA_FILES, cal.DATA_SUBDIR)
    for path, sha in hashes.items():
        print(f"  {sha[:16]}  {path}", flush=True)

    print(f"[{run_id}] archaeology", flush=True)
    arch = ArchaeologySession(life, adapter, ledger, run_id, base_snapshot="daytona-medium",
                              volumes=[(volume_id, "/data")])
    try:
        t0 = time.monotonic()
        cal.build_environment(arch)
        print(f"  environment built in {time.monotonic() - t0:.0f}s", flush=True)
        arch.smoke()
        print("  smoke gate passed", flush=True)
        snapshot_name = f"s0-fashion-mnist-{run_id}"
        t0 = time.monotonic()
        frozen = arch.freeze(snapshot_name)
        print(f"  frozen {frozen['snapshot']} in {time.monotonic() - t0:.0f}s "
              f"(recipe {frozen['recipe_sha'][:16]}, git {frozen['git_sha'][:12]})", flush=True)
        t0 = time.monotonic()
        arch.verify_s0_boot(snapshot_name)
        print(f"  fresh boot from S0 passed smoke in {time.monotonic() - t0:.0f}s", flush=True)
    finally:
        arch.teardown()
        print("  archaeology sandbox deleted", flush=True)

    handle = {
        "run_id": run_id, "run_dir": str(run_dir), "s0_snapshot": snapshot_name,
        "volume_id": volume_id, "dataset_hashes": hashes,
        "prereg_hash": prereg_hash, "annex_hash": annex_hash,
        "recipe_sha": frozen["recipe_sha"], "ledger": str(RUN_ROOT / "ledger.db"),
    }
    (RUN_ROOT / "latest.json").write_text(json.dumps(handle, indent=2))
    print(json.dumps(handle, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""P2+P3 on the calibration paper: run every preregistered experiment (visible and
held-out) as its own sandbox from S0, plus the standing controls — a sham twin with
deterministically corrupted targets (expected NOT REPRODUCED) and a hermeticity run
with all networking blocked (expected to complete). Then score everything against
the frozen preregistration and print the verdict table.
"""

import concurrent.futures as cf
import hashlib
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
from repro.orchestrator.prereg import canonical_json, load_prereg  # noqa: E402
from repro.pipeline import p3_verdict as p3  # noqa: E402
from repro.pipeline.p2_experiments import run_experiment  # noqa: E402

RUN_ROOT = Path("runs/calibration")

TTL_MIN = {"E001": 30, "E002": 60, "E003": 60, "E004": 20, "E005": 30, "E006": 30,
           "E101": 40, "E102": 30, "SH01": 20, "HERM": 20}
SHAM_DELTA = 0.05  # deterministic corruption applied to the sham twin's targets
PARALLEL_SANDBOXES = 4


def main() -> int:
    handle = json.loads((RUN_ROOT / "latest.json").read_text())
    run_id = handle["run_id"]
    run_dir = Path(handle["run_dir"])
    prereg, prereg_hash = load_prereg(run_dir / "prereg.json")
    assert prereg_hash == handle["prereg_hash"], "prereg drifted since freeze"
    annex = json.loads((run_dir / "prereg_annex.json").read_text())
    annex_hash = handle["annex_hash"]

    ledger = Ledger(handle["ledger"])
    gates = Gates(ledger)
    gates.require(run_id, "G1")
    budget = Budget(ledger, run_id, {"sandbox_minutes": 4000, "parallel_calls": 12})
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, budget, run_id)
    evidence_root = run_dir / "evidence"
    common = dict(life=life, adapter=adapter, ledger=ledger, run_id=run_id,
                  s0_snapshot=handle["s0_snapshot"], dataset_hashes=handle["dataset_hashes"],
                  evidence_root=evidence_root)

    jobs = []
    for doc, doc_hash in ((prereg, prereg_hash), (annex, annex_hash)):
        for entry in doc["experiments"]:
            exp_id = entry["experiment_id"]
            manifest = build_manifest(doc, doc_hash, exp_id,
                                      budget={"ttl_min": TTL_MIN.get(exp_id, 45),
                                              "cpu": 2, "memory_gib": 4})
            jobs.append((exp_id, dict(common, prereg=doc, prereg_hash=doc_hash,
                                      manifest=manifest)))

    print(f"[{run_id}] running {len(jobs)} preregistered experiments "
          f"({PARALLEL_SANDBOXES} sandboxes at a time)", flush=True)
    failures = {}
    with cf.ThreadPoolExecutor(max_workers=PARALLEL_SANDBOXES) as pool:
        futures = {pool.submit(run_experiment, **kw): exp_id for exp_id, kw in jobs}
        for fut in cf.as_completed(futures):
            exp_id = futures[fut]
            try:
                m = fut.result()
                print(f"  {exp_id} mean={m['mean_value']} over {m['n_seeds']} seeds", flush=True)
            except Exception as e:
                failures[exp_id] = str(e)
                print(f"  {exp_id} FAILED: {str(e)[:300]}", flush=True)

    # sham twin: corrupted targets, cheapest claim, fresh sandbox, same S0
    sham_doc, sham_hash = _sham_prereg(prereg)
    sham_manifest = build_manifest(sham_doc, sham_hash, "SH01",
                                   budget={"ttl_min": TTL_MIN["SH01"], "cpu": 2, "memory_gib": 4})
    ledger.log_event(run_id, "sham_defined", {"delta": SHAM_DELTA, "hash": sham_hash})
    try:
        m = run_experiment(prereg=sham_doc, prereg_hash=sham_hash, manifest=sham_manifest, **common)
        print(f"  SH01 (sham) mean={m['mean_value']}", flush=True)
    except Exception as e:
        failures["SH01"] = str(e)
        print(f"  SH01 FAILED: {str(e)[:300]}", flush=True)

    # hermeticity: the cheapest reproduce experiment, network_block_all at creation
    herm_entry = dict(next(e for e in prereg["experiments"] if e["experiment_id"] == "E004"))
    herm_doc = dict(prereg, experiments=[dict(herm_entry, experiment_id="HERM",
                                              command="bash runner.sh HERM")])
    herm_text = canonical_json(herm_doc)
    herm_hash = hashlib.sha256(herm_text.encode()).hexdigest()
    herm_manifest = build_manifest(herm_doc, herm_hash, "HERM",
                                   budget={"ttl_min": TTL_MIN["HERM"], "cpu": 2, "memory_gib": 4})
    try:
        m = run_experiment(prereg=herm_doc, prereg_hash=herm_hash, manifest=herm_manifest,
                           hermetic=True, **common)
        hermeticity = f"VERIFIED - network_block_all active, run completed (mean={m['mean_value']})"
    except Exception as e:
        hermeticity = f"NOT ESTABLISHED - {str(e)[:200]}"
    print(f"  hermeticity: {hermeticity}", flush=True)
    ledger.log_event(run_id, "hermeticity", {"result": hermeticity})

    # P3: verdicts against the frozen prereg + annex; sham judged against its own doc
    rows = p3.judge_run(prereg, annex, evidence_root, ledger, run_id)
    sham_rows = p3.judge_run(sham_doc, {"claims": [], "experiments": []},
                             evidence_root, ledger, run_id)
    report = {
        "run_id": run_id, "prereg_hash": prereg_hash,
        "verdicts": rows, "sham": sham_rows, "hermeticity": hermeticity,
        "failures": failures, "framing": prereg["framing"],
    }
    (run_dir / "verdicts.json").write_text(json.dumps(report, indent=2))

    print("\n| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict |")
    print("|---|---|---|---|---|---|---|")
    for r in sham_rows:
        print(f"| SH01 (sham) | {r['claim_id']} | sham | - | {r['observed']} | "
              f"{r['delta']} | {r['verdict']} |")
    for r in rows:
        print(f"| {r['experiment_id']} | {r['claim_id']} | {r['type']} | "
              f"{'yes' if r['held_out'] else 'no'} | {r['observed']} | {r['delta']} | {r['verdict']} |")
    print(f"\nhermeticity: {hermeticity}")
    return 1 if failures else 0


def _sham_prereg(prereg: dict) -> tuple[dict, str]:
    claim = next(c for c in prereg["claims"] if c["id"] == "C4")
    sham_claim = dict(claim, id="C4", reported_value=round(claim["reported_value"] + SHAM_DELTA, 3))
    entry = {
        "experiment_id": "SH01", "claim_id": "C4", "type": "reproduce",
        "command": "bash runner.sh SH01",
        "rule": {"id": "R-SH01", "kind": "abs_tolerance",
                 "target": sham_claim["reported_value"], "tolerance": 0.01,
                 "aggregate": "mean"},
    }
    doc = {"version": 1, "role": "sham_twin", "paper": prereg["paper"],
           "claims": [sham_claim], "experiments": [entry],
           "tolerances": {"C4": 0.01}, "seeds": prereg["seeds"]}
    text = canonical_json(doc)
    return doc, hashlib.sha256(text.encode()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())

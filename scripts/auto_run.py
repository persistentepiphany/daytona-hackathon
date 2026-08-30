"""Autonomous end-to-end run: paper text in, graded verdicts out, no hand-written code.

Differs from scripts/run_e2e.py in the two places that matter: the preregistration
comes from the Planner's proposal rather than a hand-written calibration module,
and the environment plus the candidate implementation come from the Implementer
rather than `cal.build_environment`. Everything downstream - the P2 executor, the
P3 grader, the controls - is the existing machinery, untouched.

Usage: python scripts/auto_run.py [paper_dir] [--seeds 17,41,93]
"""

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repro.auto.build import MAX_ITERATIONS, build_to_smoke  # noqa: E402
from repro.auto.contract import prereg_inputs  # noqa: E402
from repro.auto.provider import make_auto_provider  # noqa: E402
from repro.env import env_key  # noqa: E402
from repro.orchestrator.budget import Budget  # noqa: E402
from repro.orchestrator.daytona_client import DaytonaAdapter  # noqa: E402
from repro.orchestrator.gates import Gates  # noqa: E402
from repro.orchestrator.ledger import Ledger  # noqa: E402
from repro.orchestrator.lifecycle import Lifecycle  # noqa: E402
from repro.orchestrator.manifest import build_manifest  # noqa: E402
from repro.orchestrator.prereg import build_prereg, freeze_prereg  # noqa: E402
from repro.pipeline import p3_verdict as p3  # noqa: E402
from repro.pipeline.p0_intake import classify_paper, evaluate_code_existence, intake_decision  # noqa: E402
from repro.pipeline.p1_archaeology import ArchaeologySession  # noqa: E402
from repro.pipeline.p2_experiments import run_experiment  # noqa: E402
from repro.pipeline.report import generate_report  # noqa: E402
from repro.roles import planner as planner_role  # noqa: E402

RUN_ROOT = Path("runs/auto")
TTL_MIN = 20
BUDGET = {"sandbox_minutes": 1500, "parallel_calls": 6}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paper_dir", nargs="?", default="papers/fashion-mnist")
    ap.add_argument("--seeds", default="17,41,93")
    args = ap.parse_args()

    secrets = [s for s in (env_key("ZAI_API_KEY", "ZAI_API"),
                           env_key("DAYTONA_API_KEY", "DAYTONA_API"),
                           env_key("PARALLEL_API_KEY", "PARALLEL_API")) if s]
    seeds = [int(s) for s in args.seeds.split(",")]
    paper_dir = Path(args.paper_dir)
    paper = json.loads((paper_dir / "paper.json").read_text())
    paper_text = (paper_dir / "paper-extract.txt").read_text()

    run_id = f"auto-{int(time.time())}"
    run_dir = RUN_ROOT / run_id
    (run_dir / "evidence").mkdir(parents=True, exist_ok=True)
    ledger = Ledger(RUN_ROOT / "ledger.db")
    provider = make_auto_provider("planner")

    # ---- P0 intake ---------------------------------------------------------
    classification = classify_paper(provider, paper_text)
    log(f"P0 paper class {classification['paper_class']} ({classification['label']})")
    code_absence = json.loads((paper_dir / "code_absence.json").read_text())
    outcome, certificate = evaluate_code_existence(
        code_absence["results"], link_alive={r["url"]: True for r in code_absence["results"]})
    decision = intake_decision(classification["paper_class"], outcome)
    override = not decision["proceed"] and outcome == "FOUND"
    log(f"P0 code gate {outcome} -> {decision['reason']}"
        + (" [calibration override]" if override else ""))

    # ---- Planner -> preregistration ---------------------------------------
    proposal = planner_role.propose(provider, paper_text,
                                    "reproduce the paper's headline benchmark rows", "quick")
    log(f"planner: {len(proposal['claims'])} claims, "
        f"{len(proposal.get('experiments', []))} experiments, "
        f"{len(proposal.get('ambiguities', []))} ambiguities")
    paper_h, claims, experiments, tolerances, seeds = prereg_inputs(paper, proposal, seeds)
    log(f"contract: claims {[c['id'] for c in claims]} "
        f"targets {[c['reported_value'] for c in claims]} tolerances {tolerances}")
    doc, annex = build_prereg(paper_h, claims, experiments, tolerances, seeds,
                              held_out_fraction=0, rng_seed=1337)
    prereg_hash = freeze_prereg(doc, run_dir)
    ledger.create_run(run_id, paper_hash=paper["pdf_sha256"], prereg_hash=prereg_hash)
    ledger.log_event(run_id, "intake", {"kind": "paper_class", **classification})
    ledger.log_event(run_id, "planner_proposal",
                     {"claims": len(proposal["claims"]), "kept": [c["id"] for c in claims]})
    gates = Gates(ledger)
    gates.approve(run_id, "G1", "auto-driver")
    budget = Budget(ledger, run_id, BUDGET)
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, budget, run_id)
    log(f"G1 prereg frozen {prereg_hash[:16]} (seeds {seeds})")

    # ---- Implementer -> archaeology ---------------------------------------
    arch = ArchaeologySession(life, adapter, ledger, run_id, base_snapshot="daytona-medium")
    build_result, s0, frozen = None, None, None
    try:
        log(f"P1 implementer build loop (cap {MAX_ITERATIONS} rounds)")
        build_result = build_to_smoke(arch, provider, paper_text, ledger, run_id,
                                      secrets, claims=doc["claims"],
                                      candidate_dir=run_dir / "candidate", log=log)
        if build_result["ok"]:
            s0 = f"s0-{run_id}"
            frozen = arch.freeze(s0)
            arch.verify_s0_boot(s0)
            log(f"P1 S0 frozen {s0} (recipe {frozen['recipe_sha'][:12]})")
    finally:
        arch.teardown()

    (run_dir / "build.json").write_text(json.dumps(
        {"result": build_result, "proposal_claims": [c["id"] for c in claims]}, indent=2))
    if not build_result or not build_result["ok"]:
        log(f"P1 FAILED after {MAX_ITERATIONS} rounds; no S0. Run recorded, no experiments run.")
        (run_dir / "verdicts.json").write_text(json.dumps(
            {"run_id": run_id, "prereg_hash": prereg_hash, "verdicts": [], "sham": [],
             "build": build_result}, indent=2))
        return 2

    # ---- P2 experiments (existing executor, synthetic mode = no staging) ----
    evidence_root = run_dir / "evidence"
    common = dict(life=life, adapter=adapter, ledger=ledger, run_id=run_id,
                  s0_snapshot=s0, dataset_hashes={}, evidence_root=evidence_root,
                  data_mode="synthetic")
    jobs = [(e["experiment_id"],
             build_manifest(doc, prereg_hash, e["experiment_id"],
                            budget={"ttl_min": TTL_MIN, "cpu": 2, "memory_gib": 4}))
            for e in doc["experiments"]]
    # one at a time: the Ledger's SQLite connection is not safe to share across
    # threads, and two concurrent experiments raced it into
    # 'cannot commit - no transaction is active'
    with cf.ThreadPoolExecutor(max_workers=1) as pool:
        futures = {pool.submit(run_experiment, prereg=doc, prereg_hash=prereg_hash,
                               manifest=m, **common): eid for eid, m in jobs}
        for fut in cf.as_completed(futures):
            eid = futures[fut]
            try:
                log(f"P2 {eid} mean={fut.result()['mean_value']}")
            except Exception as e:  # noqa: BLE001 - a failed experiment is a result
                log(f"P2 {eid} FAILED: {str(e)[:200]}")

    # ---- P3 verdicts -------------------------------------------------------
    rows = p3.judge_run(doc, annex, evidence_root, ledger, run_id)
    verdicts = {"run_id": run_id, "prereg_hash": prereg_hash, "verdicts": rows,
                "sham": [], "hermeticity": "NOT RUN - autonomous smoke path",
                "framing": doc["framing"], "build": build_result}
    (run_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=2))
    for r in rows:
        log(f"P3 {r['experiment_id']} {r['claim_id']} observed={r['observed']} -> {r['verdict']}")

    report = generate_report(run_id, doc, rows, [], verdicts["hermeticity"], ledger,
                             paper.get("title", args.paper_dir), code_absence=certificate)
    (run_dir / "report.md").write_text(report)
    handle = {"run_id": run_id, "run_dir": str(run_dir), "s0_snapshot": s0,
              "prereg_hash": prereg_hash, "autonomous": True,
              "build_iterations": build_result["iterations"]}
    (RUN_ROOT / "latest.json").write_text(json.dumps(handle, indent=2))
    log(f"done: {run_dir}")
    return 0 if rows else 3


if __name__ == "__main__":
    sys.exit(main())

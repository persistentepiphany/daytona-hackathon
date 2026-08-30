"""End-to-end pipeline run on one paper, LLM roles live, quick profile.

Stages: P0 intake (paper-class classifier + code-existence gate + planner
proposal), G1 prereg freeze, P1 archaeology to S0, P2 experiments (reduced claim
set, seeds per the quick prereg) with sham + hermeticity controls, P3 deterministic
verdicts cross-checked by the sealed verifier, P5 preview deploy. Model usage is
deliberately minimal: one classifier call, one planner call, one verifier call.

Artifacts land in runs/e2e/<run_id>/ plus a repo-ready export in
runs/e2e/<run_id>/export/ (prereg first, per the output-repo convention).
"""

import concurrent.futures as cf
import hashlib
import json
import os
import shutil
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
from repro.orchestrator.manifest import build_manifest  # noqa: E402
from repro.orchestrator.prereg import build_prereg, canonical_json, freeze_prereg  # noqa: E402
from repro.pipeline import p3_verdict as p3  # noqa: E402
from repro.pipeline.p0_intake import classify_paper, evaluate_code_existence, intake_decision  # noqa: E402
from repro.pipeline.p1_archaeology import ArchaeologySession  # noqa: E402
from repro.pipeline.p2_experiments import run_experiment  # noqa: E402
from repro.pipeline.p5_build import deploy, fallback_app_files  # noqa: E402
from repro.pipeline.report import generate_report  # noqa: E402
from repro.pipeline.staging import stage_datasets  # noqa: E402
from repro.roles import verifier as verifier_role  # noqa: E402
from repro.roles.base import make_provider  # noqa: E402

RUN_ROOT = Path("runs/e2e")
QUICK_CLAIMS = ("C1", "C4")
QUICK_SEEDS = [17, 41, 93]
SHAM_DELTA = 0.05
PAPER_TITLE = "Fashion-MNIST (arXiv:1708.07747)"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    run_id = f"e2e-{int(time.time())}"
    run_dir = RUN_ROOT / run_id
    export = run_dir / "export"
    (export / "evidence").mkdir(parents=True, exist_ok=True)
    ledger = Ledger(RUN_ROOT / "ledger.db")
    paper = json.loads(Path("papers/fashion-mnist/paper.json").read_text())
    paper_text = Path(sys.argv[1] if len(sys.argv) > 1 else
                      "papers/fashion-mnist/paper-extract.txt").read_text()

    # ---- P0: intake -------------------------------------------------------
    provider = make_provider("planner")
    classification = classify_paper(provider, paper_text)
    log(f"P0 paper class: {classification['paper_class']} ({classification['label']})")

    code_absence = json.loads(Path("papers/fashion-mnist/code_absence.json").read_text())
    outcome, certificate = evaluate_code_existence(
        code_absence["results"],
        link_alive={r["url"]: True for r in code_absence["results"]},
    )
    decision = intake_decision(classification["paper_class"], outcome)
    log(f"P0 code gate: {outcome} -> {decision['reason']}")
    calibration_override = not decision["proceed"] and outcome == "FOUND"
    if calibration_override:
        log("P0 calibration override: proceeding on the calibration paper; "
            "the decline path is the demonstrated behavior for target papers")

    from repro.roles import planner as planner_role
    try:
        proposal = planner_role.propose(provider, paper_text,
                                        "reproduce Table 3 benchmark rows", "quick")
        log(f"P0 planner proposed {len(proposal.get('claims', []))} claims, "
            f"{len(proposal.get('experiments', []))} experiments")
    except Exception as e:
        proposal = {"error": str(e)[:2000]}
        log(f"P0 planner proposal rejected by validator: {str(e)[:160]}")

    # ---- G1: quick prereg -------------------------------------------------
    _, claims, experiments, tolerances, _ = cal.prereg_inputs()
    claims = [c for c in claims if c["id"] in QUICK_CLAIMS]
    experiments = [e for e in experiments if e["claim_id"] in QUICK_CLAIMS
                   and e["type"] == "reproduce"]
    tolerances = {k: v for k, v in tolerances.items() if k in QUICK_CLAIMS}
    doc, annex = build_prereg(paper, claims, experiments, tolerances, QUICK_SEEDS,
                              held_out_fraction=0, rng_seed=1337)
    prereg_hash = freeze_prereg(doc, run_dir)
    ledger.create_run(run_id, paper_hash=paper["pdf_sha256"], prereg_hash=prereg_hash)
    for payload in (
        {"kind": "paper_class", **classification},
        {"kind": "code_gate", "outcome": outcome, "decision": decision,
         "calibration_override": calibration_override},
    ):
        ledger.log_event(run_id, "intake", payload)
    gates = Gates(ledger)
    gates.approve(run_id, "G1", "user")
    budget = Budget(ledger, run_id, {"sandbox_minutes": 1500, "parallel_calls": 12})
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, budget, run_id)
    log(f"G1 prereg frozen {prereg_hash[:16]} ({len(claims)} claims, seeds {QUICK_SEEDS})")

    # ---- P1: archaeology to S0 -------------------------------------------
    volume_id = adapter.volume_ensure("datasets")
    hashes = stage_datasets(life, adapter, ledger, run_id, "daytona-small", "datasets",
                            cal.DATA_FILES, cal.DATA_SUBDIR)
    arch = ArchaeologySession(life, adapter, ledger, run_id, base_snapshot="daytona-medium",
                              volumes=[(volume_id, "/data")])
    try:
        cal.build_environment(arch)
        arch.smoke()
        s0 = f"s0-{run_id}"
        frozen = arch.freeze(s0)
        arch.verify_s0_boot(s0)
        log(f"P1 S0 frozen: {s0} (recipe {frozen['recipe_sha'][:12]})")
    finally:
        arch.teardown()

    # ---- P2: experiments + controls --------------------------------------
    evidence_root = run_dir / "evidence"
    common = dict(life=life, adapter=adapter, ledger=ledger, run_id=run_id,
                  s0_snapshot=s0, dataset_hashes=hashes, evidence_root=evidence_root)
    ttl = {"E001": 25, "E004": 10}
    jobs = [(e["experiment_id"],
             build_manifest(doc, prereg_hash, e["experiment_id"],
                            budget={"ttl_min": ttl.get(e["experiment_id"], 20),
                                    "cpu": 2, "memory_gib": 4}))
            for e in doc["experiments"]]
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(run_experiment, prereg=doc, prereg_hash=prereg_hash,
                               manifest=m, **common): eid for eid, m in jobs}
        for fut in cf.as_completed(futures):
            eid = futures[fut]
            try:
                log(f"P2 {eid} mean={fut.result()['mean_value']}")
            except Exception as e:
                log(f"P2 {eid} FAILED: {str(e)[:200]}")

    sham_claim = next(c for c in claims if c["id"] == "C1")
    sham_doc = {"version": 1, "role": "sham_twin", "paper": doc["paper"],
                "claims": [dict(sham_claim, reported_value=round(sham_claim["reported_value"] + SHAM_DELTA, 3))],
                "experiments": [{"experiment_id": "SH01", "claim_id": "C1",
                                 "type": "reproduce", "command": "bash runner.sh SH01",
                                 "condition": sham_claim.get("condition"),
                                 "rule": {"id": "R-SH01", "kind": "abs_tolerance",
                                          "target": round(sham_claim["reported_value"] + SHAM_DELTA, 3),
                                          "tolerance": 0.01, "aggregate": "mean"}}],
                "tolerances": {"C1": 0.01}, "seeds": QUICK_SEEDS}
    sham_hash = hashlib.sha256(canonical_json(sham_doc).encode()).hexdigest()
    sham_manifest = build_manifest(sham_doc, sham_hash, "SH01",
                                   budget={"ttl_min": 25, "cpu": 2, "memory_gib": 4})
    try:
        m = run_experiment(prereg=sham_doc, prereg_hash=sham_hash, manifest=sham_manifest, **common)
        log(f"P2 SH01 (sham) mean={m['mean_value']}")
    except Exception as e:
        log(f"P2 SH01 FAILED: {str(e)[:200]}")

    herm_entry = dict(next(e for e in doc["experiments"] if e["claim_id"] == "C4"),
                      experiment_id="HERM", command="bash runner.sh HERM")
    herm_doc = dict(doc, experiments=[herm_entry])
    herm_hash = hashlib.sha256(canonical_json(herm_doc).encode()).hexdigest()
    herm_manifest = build_manifest(herm_doc, herm_hash, "HERM",
                                   budget={"ttl_min": 10, "cpu": 2, "memory_gib": 4})
    try:
        m = run_experiment(prereg=herm_doc, prereg_hash=herm_hash, manifest=herm_manifest,
                           hermetic=True, **common)
        hermeticity = f"VERIFIED - network_block_all active, run completed (mean={m['mean_value']})"
    except Exception as e:
        hermeticity = f"NOT ESTABLISHED - {str(e)[:180]}"
    log(f"P2 hermeticity: {hermeticity}")

    # ---- P3: verdicts + sealed verifier -----------------------------------
    rows = p3.judge_run(doc, annex, evidence_root, ledger, run_id)
    sham_rows = p3.judge_run(sham_doc, {"claims": [], "experiments": []},
                             evidence_root, ledger, run_id)
    verdicts = {"run_id": run_id, "prereg_hash": prereg_hash, "verdicts": rows,
                "sham": sham_rows, "hermeticity": hermeticity,
                "framing": doc["framing"]}
    try:
        review = verifier_role.verify(make_provider("verifier"), doc, evidence_root)
        disagreements = verifier_role.cross_check(review, rows + sham_rows)
        verdicts["verifier_review"] = review
        verdicts["verifier_disagreements"] = disagreements
        log(f"P3 sealed verifier: {len(review.get('verdicts', []))} verdicts, "
            f"{len(disagreements)} disagreement(s) with the engine")
    except Exception as e:
        verdicts["verifier_review"] = {"error": str(e)[:1000]}
        log(f"P3 verifier unavailable: {str(e)[:160]}")
    (run_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=2))
    for r in sham_rows + rows:
        log(f"P3 {r['experiment_id']} {r['claim_id']} observed={r['observed']} -> {r['verdict']}")

    # ---- P5: preview ------------------------------------------------------
    lineage = {"run_id": run_id, "prereg": prereg_hash, "s0": s0,
               "recipe": frozen["recipe_sha"]}
    files = fallback_app_files(sham_rows + rows, hermeticity, PAPER_TITLE, lineage)
    deployment = deploy(life, adapter, ledger, run_id, files, demo_window=True)
    log(f"P5 preview: {deployment['preview_url']}")

    # ---- export (repo-ready) ---------------------------------------------
    report = generate_report(run_id, doc, rows, sham_rows, hermeticity, ledger,
                             PAPER_TITLE, code_absence=certificate)
    (run_dir / "report.md").write_text(report)
    shutil.copy2(run_dir / "prereg.json", export / "prereg.json")
    for name in ("verdicts.json", "report.md"):
        shutil.copy2(run_dir / name, export / name)
    (export / "intake.json").write_text(json.dumps({
        "classification": classification, "code_gate": certificate,
        "decision": decision, "calibration_override": calibration_override}, indent=2))
    (export / "planner_proposal.json").write_text(json.dumps(proposal, indent=2))
    (export / "deployment.json").write_text(json.dumps(deployment, indent=2))
    if evidence_root.exists():
        shutil.copytree(evidence_root, export / "evidence", dirs_exist_ok=True)
    handle = {"run_id": run_id, "run_dir": str(run_dir), "export": str(export),
              "s0_snapshot": s0, "prereg_hash": prereg_hash, "deployment": deployment}
    (RUN_ROOT / "latest.json").write_text(json.dumps(handle, indent=2))
    log(f"export ready: {export}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

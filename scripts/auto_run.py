"""Autonomous end-to-end run: paper text in, graded verdicts out, no hand-written code.

Differs from scripts/run_e2e.py in the two places that matter: the preregistration
comes from the Planner's proposal rather than a hand-written calibration module,
and the environment plus the candidate implementation come from the Implementer
rather than `cal.build_environment`. Everything downstream - the P2 executor, the
P3 grader, the controls - is the existing machinery, untouched.

Usage: python scripts/auto_run.py [paper_dir] [--seeds 17,41,93] [--run-id auto-…]

Safe to run several at once, one process per paper: run ids are unique per
process, the ledger is shared through WAL, and sandbox creates queue on the org
quota instead of failing. scripts/fanout.py drives that fan-out.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Callable
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
from repro.orchestrator.parallel_client import ParallelClient  # noqa: E402
from repro.orchestrator.prereg import build_prereg, canonical_json, freeze_prereg  # noqa: E402
from repro.pipeline import p3_verdict as p3  # noqa: E402
from repro.pipeline.p0_intake import classify_paper, evaluate_code_existence, intake_decision  # noqa: E402
from repro.pipeline.p1_archaeology import ArchaeologySession  # noqa: E402
from repro.pipeline.p2_experiments import run_experiment  # noqa: E402
from repro.pipeline.report import generate_report  # noqa: E402
from repro.pipeline.staging import StagingError, stage_datasets  # noqa: E402
from repro.roles import planner as planner_role  # noqa: E402

RUN_ROOT = Path("runs/auto")
TTL_MIN = 20
BUDGET = {"sandbox_minutes": 1500, "parallel_calls": 6}

LogFn = Callable[[str], None]


def _default_log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_auto(
    paper_dir: str | Path,
    seeds: list[int] | None = None,
    run_id: str | None = None,
    log: LogFn | None = None,
    base_snapshot: str = "daytona-medium",
    max_experiment_workers: int = 1,
) -> int:
    """Run the autonomous pipeline. Returns the CLI exit code (0/2/3)."""
    log = log or _default_log
    seeds = list(seeds or [17, 41, 93])
    paper_dir = Path(paper_dir)
    paper = json.loads((paper_dir / "paper.json").read_text())
    paper_text = (paper_dir / "paper-extract.txt").read_text()

    secrets = [s for s in (env_key("ZAI_API_KEY", "ZAI_API"),
                           env_key("DAYTONA_API_KEY", "DAYTONA_API"),
                           env_key("PARALLEL_API_KEY", "PARALLEL_API")) if s]

    run_id = run_id or f"auto-{int(time.time())}-{uuid.uuid4().hex[:6]}"
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
    log(f"P0 code gate {outcome} -> {decision['reason']}")
    if not decision["proceed"]:
        # FOUND is a successful intake outcome, not permission to ignore the
        # wedge criterion. Preserve a complete, publishable terminal record
        # without creating a Daytona sandbox or presenting measurements.
        terminal = {
            "run_id": run_id,
            "classification": "DECLINED_CODE_FOUND" if outcome == "FOUND" else "NOT ATTEMPTABLE",
            "paper_class": classification,
            "code_absence": certificate,
            "reason": decision["reason"],
        }
        (run_dir / "intake.json").write_text(json.dumps(terminal, indent=2))
        (run_dir / "verdicts.json").write_text(json.dumps({
            "run_id": run_id, "verdicts": [], "framing": decision["reason"],
            "terminal_classification": terminal["classification"],
        }, indent=2))
        (run_dir / "report.md").write_text(
            f"# Reproduction intake: {paper.get('title', paper_dir.name)}\n\n"
            f"**Outcome:** {terminal['classification']}\n\n{decision['reason']}\n\n"
            "No Daytona compute was started. The code-search certificate is in `intake.json`.\n"
        )
        log(f"done: {run_dir} ({terminal['classification']})")
        return 4

    # ---- Planner -> preregistration ---------------------------------------
    proposal = planner_role.propose(provider, paper_text,
                                    "reproduce the paper's headline benchmark rows", "quick")
    log(f"planner: {len(proposal['claims'])} claims, "
        f"{len(proposal.get('experiments', []))} experiments, "
        f"{len(proposal.get('ambiguities', []))} ambiguities")
    data_requirements = proposal.get("data_requirements") or []
    unresolved = [item.get("id", "dataset") for item in data_requirements
                  if item.get("required", True) and not item.get("url")]
    if unresolved:
        terminal = {"run_id": run_id, "classification": "UNDER_CONSTRAINED",
                    "reason": f"required datasets have no paper-declared URL: {unresolved}"}
        (run_dir / "verdicts.json").write_text(json.dumps({
            **terminal, "verdicts": [], "sham": [],
            "hermeticity": "NOT RUN - required data unresolved",
        }, indent=2))
        (run_dir / "report.md").write_text(
            f"# Reproduction report: {paper.get('title', paper_dir.name)}\n\n"
            f"**Outcome:** UNDER_CONSTRAINED\n\n{terminal['reason']}\n"
        )
        log(f"done: {run_dir} (UNDER_CONSTRAINED: unresolved data; G1 not approved)")
        return 3
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
    gates.approve(run_id, "G1", "policy:auto")
    (run_dir / "intake.json").write_text(json.dumps({
        "classification": classification, "code_gate": certificate,
        "decision": decision, "g1_approver": "policy:auto",
    }, indent=2))
    (run_dir / "planner_proposal.json").write_text(json.dumps(proposal, indent=2))
    budget = Budget(ledger, run_id, BUDGET)
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, budget, run_id)
    log(f"G1 prereg frozen {prereg_hash[:16]} (seeds {seeds})")

    # ---- Implementer -> archaeology ---------------------------------------
    dataset_hashes: dict[str, str] = {}
    volumes: list[tuple[str, str]] = []
    data_mode = "synthetic"
    staged_note = ""
    if data_requirements:
        files = {item.get("filename") or item["id"]: item["url"] for item in data_requirements}
        volume_name = f"datasets-{run_id}"[:48]
        try:
            volume_id = adapter.volume_ensure(volume_name)
            dataset_hashes = stage_datasets(life, adapter, ledger, run_id, base_snapshot,
                                             volume_name, files, run_id)
            for item in data_requirements:
                expected = item.get("sha256")
                name = item.get("filename") or item["id"]
                observed = dataset_hashes.get(f"{run_id}/{name}")
                if expected and observed != expected.lower():
                    raise StagingError(f"declared checksum mismatch for {name}")
                license_name = str(item.get("license") or "unknown").lower()
                if any(word in license_name for word in ("proprietary", "restricted", "no redistribution")):
                    raise StagingError(f"dataset license does not permit automated staging: {license_name}")
        except Exception as exc:
            try:
                adapter.volume_delete(volume_name)
            except Exception:
                pass
            (run_dir / "verdicts.json").write_text(json.dumps({
                "run_id": run_id, "prereg_hash": prereg_hash, "verdicts": [],
                "terminal_classification": "NOT ATTEMPTABLE",
                "framing": f"required dataset unavailable: {exc}",
            }, indent=2))
            (run_dir / "report.md").write_text(
                f"# Reproduction report: {paper.get('title', paper_dir.name)}\n\n"
                f"**Outcome:** NOT ATTEMPTABLE\n\nRequired dataset unavailable: {exc}\n"
            )
            log(f"done: {run_dir} (NOT ATTEMPTABLE: dataset staging failed)")
            return 2
        volumes = [(volume_id, "/data")]
        data_mode = "staged"
        staged_note = (f"\n\nControl-plane verified dataset files are mounted under "
                       f"/data/{run_id}/. Copy them to ./localdata; do not download them.")

    arch = ArchaeologySession(life, adapter, ledger, run_id, base_snapshot=base_snapshot,
                              volumes=volumes)
    build_result, s0, frozen = None, None, None
    try:
        log(f"P1 implementer build loop (cap {MAX_ITERATIONS} rounds)")
        # the role can now search for a working mirror instead of re-trying a
        # host that is unreachable from this sandbox
        parallel = ParallelClient(ledger, run_id, budget)
        build_result = build_to_smoke(arch, provider, paper_text + staged_note, ledger, run_id,
                                      secrets, claims=doc["claims"],
                                      candidate_dir=run_dir / "candidate",
                                      parallel=parallel, log=log)
        if build_result["ok"]:
            s0 = f"s0-{run_id}"
            frozen = arch.freeze(s0)
            arch.verify_s0_boot(s0)
            log(f"P1 S0 frozen {s0} (recipe {frozen['recipe_sha'][:12]})")
    finally:
        try:
            arch.teardown()
        finally:
            if data_mode == "staged":
                try:
                    adapter.volume_delete(volume_name)
                    ledger.log_event(run_id, "dataset_volume_deleted", {"volume": volume_name})
                except Exception as exc:  # cleanup failure is visible, not scientific evidence
                    ledger.log_event(run_id, "dataset_volume_cleanup_failed",
                                     {"volume": volume_name, "error": str(exc)[:300]})

    (run_dir / "build.json").write_text(json.dumps(
        {"result": build_result, "proposal_claims": [c["id"] for c in claims]}, indent=2))
    if not build_result or not build_result["ok"]:
        lost = bool(build_result and build_result.get("environment_lost"))
        why = ("NOT RUN - the archaeology sandbox was deleted mid-build; this is an "
               "environment failure, not a failed reproduction"
               if lost else "NOT RUN - build never passed the smoke gate")
        log(f"P1 FAILED after {build_result['iterations'] if build_result else 0} rounds"
            + (" (environment lost)" if lost else "") + "; no S0.")
        rows = [{"experiment_id": e["experiment_id"], "claim_id": e["claim_id"],
                 "rule_id": e["rule"].get("id"), "type": e["type"], "observed": None,
                 "delta": None, "verdict": "NOT ATTEMPTABLE", "held_out": False,
                 "attempt_ids": []} for e in doc["experiments"]]
        (run_dir / "verdicts.json").write_text(json.dumps(
            {"run_id": run_id, "prereg_hash": prereg_hash, "verdicts": rows, "sham": [],
             "hermeticity": why, "environment_lost": lost,
             "framing": doc["framing"], "build": build_result}, indent=2))
        report = generate_report(run_id, doc, rows, [], why, ledger,
                                 paper.get("title", str(paper_dir)), code_absence=certificate)
        (run_dir / "report.md").write_text(report)
        (run_dir / "handle.json").write_text(json.dumps(
            {"run_id": run_id, "run_dir": str(run_dir), "s0_snapshot": None,
             "prereg_hash": prereg_hash, "autonomous": True,
             "paper_dir": str(paper_dir), "failed_at": "P1"}, indent=2))
        log(f"deliverable written despite failure: {run_dir} "
            f"(candidate source under candidate/)")
        return 2

    # ---- P2 experiments ---------------------------------------------------
    evidence_root = run_dir / "evidence"
    common = dict(life=life, adapter=adapter, ledger=ledger, run_id=run_id,
                  s0_snapshot=s0, dataset_hashes=dataset_hashes, evidence_root=evidence_root,
                  data_mode=data_mode)
    jobs = [(e["experiment_id"],
             build_manifest(doc, prereg_hash, e["experiment_id"],
                            budget={"ttl_min": TTL_MIN, "cpu": 2, "memory_gib": 4}))
            for e in doc["experiments"]]
    # the ledger race that forced serialization here was Budget writing outside the
    # ledger lock; that is fixed, so this is now a quota decision, not a safety one
    with cf.ThreadPoolExecutor(max_workers=max(1, max_experiment_workers)) as pool:
        futures = {pool.submit(run_experiment, prereg=doc, prereg_hash=prereg_hash,
                               manifest=m, **common): eid for eid, m in jobs}
        for fut in cf.as_completed(futures):
            eid = futures[fut]
            try:
                log(f"P2 {eid} mean={fut.result()['mean_value']}")
            except Exception as e:  # noqa: BLE001 - a failed experiment is a result
                log(f"P2 {eid} FAILED: {str(e)[:200]}")

    # Standing controls use the same frozen S0 and executor. They are meaningful
    # even for a degraded synthetic-data run: the sham checks false positives and
    # hermeticity verifies that execution does not depend on sandbox egress.
    sham_rows: list[dict] = []
    first_claim = doc["claims"][0] if doc.get("claims") else None
    first_exp = doc["experiments"][0] if doc.get("experiments") else None
    if first_claim and first_exp and isinstance(first_claim.get("reported_value"), (int, float)):
        tolerance = float(doc["tolerances"].get(first_claim["id"], 0.01))
        sham_target = float(first_claim["reported_value"]) + max(0.05, tolerance * 4)
        sham_claim = dict(first_claim, reported_value=sham_target)
        sham_exp = dict(first_exp, experiment_id="SHAM", claim_id=first_claim["id"],
                        type="reproduce", command="bash runner.sh SHAM",
                        rule={"id": "R-SHAM", "kind": "abs_tolerance", "target": sham_target,
                              "tolerance": tolerance, "aggregate": "mean"})
        sham_doc = {**doc, "role": "sham_twin", "claims": [sham_claim],
                    "experiments": [sham_exp], "tolerances": {first_claim["id"]: tolerance}}
        sham_hash = hashlib.sha256(canonical_json(sham_doc).encode()).hexdigest()
        sham_manifest = build_manifest(sham_doc, sham_hash, "SHAM",
                                       budget={"ttl_min": TTL_MIN, "cpu": 2, "memory_gib": 4})
        try:
            metric = run_experiment(prereg=sham_doc, prereg_hash=sham_hash,
                                    manifest=sham_manifest, **common)
            log(f"P2 SHAM mean={metric['mean_value']}")
        except Exception as e:  # noqa: BLE001
            log(f"P2 SHAM FAILED: {str(e)[:200]}")
        sham_rows = p3.judge_run(sham_doc, {"claims": [], "experiments": []},
                                 evidence_root, ledger, run_id)

    hermeticity = "NOT ESTABLISHED - no experiment was available for the control"
    if first_exp:
        herm_exp = dict(first_exp, experiment_id="HERM", command="bash runner.sh HERM")
        herm_doc = {**doc, "experiments": [herm_exp]}
        herm_hash = hashlib.sha256(canonical_json(herm_doc).encode()).hexdigest()
        herm_manifest = build_manifest(herm_doc, herm_hash, "HERM",
                                       budget={"ttl_min": TTL_MIN, "cpu": 2, "memory_gib": 4})
        try:
            metric = run_experiment(prereg=herm_doc, prereg_hash=herm_hash,
                                    manifest=herm_manifest, hermetic=True, **common)
            hermeticity = ("VERIFIED - network_block_all active, run completed "
                           f"(mean={metric['mean_value']})")
        except Exception as e:  # noqa: BLE001
            hermeticity = f"NOT ESTABLISHED - {str(e)[:180]}"
    log(f"P2 hermeticity: {hermeticity}")

    # ---- P3 verdicts -------------------------------------------------------
    rows = p3.judge_run(doc, annex, evidence_root, ledger, run_id)
    # Autonomous planning currently has no verified data manifest. Generated
    # or sandbox-downloaded data may exercise the implementation, but cannot be
    # graded as a reproduction of the paper's dataset.
    degraded = data_mode == "synthetic"
    if degraded:
        # the run executed against generated data, not the paper's; the observed
        # values are real measurements of the candidate code and nothing more
        for r in rows:
            r["graded_verdict_withheld"] = r["verdict"]
            r["verdict"] = "NOT COMPARABLE - synthetic data substitute"
        log("DEGRADED: synthetic data was substituted; verdicts are not comparable "
            "to the paper")
    verdicts = {"run_id": run_id, "prereg_hash": prereg_hash, "verdicts": rows,
                "sham": sham_rows, "hermeticity": hermeticity,
                "degraded": degraded,
                "framing": doc["framing"], "build": build_result}
    (run_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=2))
    for r in rows:
        log(f"P3 {r['experiment_id']} {r['claim_id']} observed={r['observed']} -> {r['verdict']}")

    report = generate_report(run_id, doc, rows, sham_rows, verdicts["hermeticity"], ledger,
                             paper.get("title", str(paper_dir)), code_absence=certificate)
    (run_dir / "report.md").write_text(report)
    handle = {"run_id": run_id, "run_dir": str(run_dir), "s0_snapshot": s0,
              "prereg_hash": prereg_hash, "autonomous": True,
              "paper_dir": str(paper_dir),
              "build_iterations": build_result["iterations"]}
    # the per-run handle is authoritative; latest.json stays for the single-run
    # path but is last-writer-wins when several pipelines finish together
    (run_dir / "handle.json").write_text(json.dumps(handle, indent=2))
    (RUN_ROOT / "latest.json").write_text(json.dumps(handle, indent=2))
    log(f"done: {run_dir}")
    return 3 if degraded or not rows else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paper_dir", nargs="?", default="papers/fashion-mnist")
    ap.add_argument("--seeds", default="17,41,93")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--base-snapshot", default="daytona-medium",
                    help="base for S0 (daytona-small 1cpu/1GiB/3GiB disk fits three "
                         "concurrent runs in the org quota; daytona-medium is 2/4/8)")
    ap.add_argument("--max-experiment-workers", type=int, default=1,
                    help="experiments in flight within this run; the org quota is "
                         "the real ceiling, so leave at 1 when running several papers")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    return run_auto(args.paper_dir, seeds=seeds, run_id=args.run_id,
                    base_snapshot=args.base_snapshot,
                    max_experiment_workers=args.max_experiment_workers)


if __name__ == "__main__":
    sys.exit(main())

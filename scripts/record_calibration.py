"""A real calibration-shaped run, recorded so the feed has a genuine stream to replay.

Runs the actual pipeline against live Daytona — G1 freeze, data staging, archaeology to
S0, experiments from S0 (including the held-out annex, a sham twin and a hermeticity
control), then verdicts — with the feed serving in the same process. The event stream is
exported afterwards so `repro feed --replay paced` can play the run back through exactly
the same code path that served it live.

Two profiles. `core` keeps the cheap models (decision trees, naive Bayes, perceptron) at
two seeds, which is a genuine run of every mechanism at a fraction of the wall clock.
`full` is the published calibration's shape. Neither replaces the published
cal-1788095064 artifacts: this is its own run with its own verdicts.

    python scripts/record_calibration.py --profile core --port 8700
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repro import feed, telemetry  # noqa: E402
from repro.calibration import fashion_mnist as cal  # noqa: E402
from repro.orchestrator.budget import Budget  # noqa: E402
from repro.orchestrator.daytona_client import DaytonaAdapter  # noqa: E402
from repro.orchestrator.gates import Gates  # noqa: E402
from repro.orchestrator.ledger import Ledger  # noqa: E402
from repro.orchestrator.lifecycle import Lifecycle  # noqa: E402
from repro.orchestrator.manifest import build_manifest  # noqa: E402
from repro.orchestrator.prereg import build_prereg, canonical_json, freeze_prereg  # noqa: E402
from repro.pipeline import p3_verdict as p3  # noqa: E402
from repro.pipeline.p1_archaeology import ArchaeologySession  # noqa: E402
from repro.pipeline.p2_experiments import run_experiment  # noqa: E402
from repro.pipeline.staging import stage_datasets  # noqa: E402

RUN_ROOT = Path("runs/recorded")
SHAM_DELTA = 0.05
POOL = 2  # the org quota is 10GiB of sandbox memory; S0 boxes are 4GiB

PROFILES = {
    # the cheap models only: decision trees, naive Bayes, perceptron. Excludes C2
    # (a 100-tree forest) and C3 (logistic regression), which dominate the wall clock
    # without exercising anything the feed does not already show.
    "core": {"claims": ("C1", "C4", "C5", "C7"), "seeds": [17, 41],
             "controls": ("E102",), "ttl": 25},
    "full": {"claims": ("C1", "C2", "C3", "C4", "C5", "C7"), "seeds": list(cal.SEEDS),
             "controls": ("E101", "E102"), "ttl": 60},
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def export_stream(ledger, run_id, path):
    """The recorded stream, one JSON object per line, ordered by the same cursor the
    SSE endpoint uses."""
    rows = ledger.events_after(run_id, 0, limit=1_000_000)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps({"id": row["id"], "kind": row["kind"],
                                "payload": json.loads(row["payload"]),
                                "t": row["created_at"]}) + "\n")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="core")
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--base-snapshot", default="daytona-medium")
    ap.add_argument("--stager-snapshot", default="daytona-small")
    ap.add_argument("--keep-sandboxes", action="store_true")
    args = ap.parse_args()
    profile = PROFILES[args.profile]

    run_id = f"rec-{int(time.time())}"
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # ---- G1: build and freeze the preregistration -------------------------
    paper, claims, experiments, tolerances, _ = cal.prereg_inputs()
    keep = set(profile["claims"])
    claims = [c for c in claims if c["id"] in keep]
    experiments = [e for e in experiments
                   if (e["claim_id"] in keep
                       and (e["type"] == "reproduce" or e["experiment_id"] in profile["controls"]))]
    tolerances = {k: v for k, v in tolerances.items() if k in keep}
    doc, annex = build_prereg(paper, claims, experiments, tolerances, profile["seeds"],
                              rng_seed=1337)
    prereg_hash = freeze_prereg(doc, run_dir)
    annex_text = canonical_json(annex)
    (run_dir / "prereg_annex.json").write_text(annex_text)
    held = [c["id"] for c in annex["claims"]]

    ledger = Ledger(RUN_ROOT / "ledger.db")
    ledger.create_run(run_id, paper_hash=paper["pdf_sha256"], prereg_hash=prereg_hash)
    gates = Gates(ledger)
    budget = Budget(ledger, run_id, {"sandbox_minutes": 4000, "parallel_calls": 12})
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, budget, run_id)

    planned = len(doc["experiments"]) + len(annex["experiments"]) + 2  # + sham + hermetic
    feed.serve_background(RUN_ROOT / "ledger.db", run_dir, args.port, bus=ledger.bus,
                          default_run=run_id, width=POOL, planned=planned)
    log(f"feed: http://127.0.0.1:{args.port}/?run_id={run_id}")
    log(f"prereg {prereg_hash[:16]} — {len(doc['claims'])} visible claims, held out {held}")

    gates.approve(run_id, "G1", "user")
    telemetry.set_tap(telemetry.ActionTap(ledger.bus, run_id, role="implementer",
                                          evidence_root=run_dir))
    s0 = None
    try:
        # ---- data staging -------------------------------------------------
        volume_id = adapter.volume_ensure("datasets")
        hashes = stage_datasets(life, adapter, ledger, run_id, args.stager_snapshot,
                                "datasets", cal.DATA_FILES, cal.DATA_SUBDIR)
        log(f"staged {len(hashes)} dataset files")

        # ---- P1 archaeology to S0 ----------------------------------------
        arch = ArchaeologySession(life, adapter, ledger, run_id,
                                  base_snapshot=args.base_snapshot,
                                  volumes=[(volume_id, "/data")])
        try:
            # the recipe drives the session directly rather than through the choke
            # point, so it is wrapped to produce the same agent events
            cal.build_environment(telemetry.tapped_session(arch, role="implementer"))
            arch.smoke()
            s0 = f"s0-{run_id}"
            frozen = arch.freeze(s0)
            arch.verify_s0_boot(s0)
            log(f"S0 frozen: {s0} (recipe {frozen['recipe_sha'][:12]})")
        finally:
            arch.teardown()

        # ---- P2 experiments ----------------------------------------------
        import concurrent.futures as cf

        evidence_root = run_dir / "evidence"
        common = dict(life=life, adapter=adapter, ledger=ledger, run_id=run_id,
                      s0_snapshot=s0, dataset_hashes=hashes, evidence_root=evidence_root)
        budget_spec = {"ttl_min": profile["ttl"], "cpu": 2, "memory_gib": 4}
        jobs = []
        for source, source_hash, held_out in (
                (doc, prereg_hash, False),
                (annex, hashlib.sha256(annex_text.encode()).hexdigest(), True)):
            for entry in source["experiments"]:
                jobs.append((entry["experiment_id"],
                             dict(common, prereg=source, prereg_hash=source_hash,
                                  manifest=build_manifest(source, source_hash,
                                                          entry["experiment_id"],
                                                          budget=budget_spec),
                                  held_out=held_out)))
        log(f"running {len(jobs)} experiments, {POOL} sandboxes at a time")
        with cf.ThreadPoolExecutor(max_workers=POOL) as pool:
            futures = {pool.submit(run_experiment, **kw): eid for eid, kw in jobs}
            for fut in cf.as_completed(futures):
                eid = futures[fut]
                try:
                    log(f"  {eid} mean={fut.result()['mean_value']}")
                except Exception as e:
                    log(f"  {eid} FAILED: {str(e)[:200]}")

        # ---- sham twin: corrupted target, expected NOT REPRODUCED ---------
        sham_doc, sham_hash = _sham(doc, profile["seeds"])
        ledger.log_event(run_id, "sham_defined", {"delta": SHAM_DELTA, "hash": sham_hash})
        try:
            m = run_experiment(prereg=sham_doc, prereg_hash=sham_hash,
                               manifest=build_manifest(sham_doc, sham_hash, "SH01",
                                                       budget=budget_spec), **common)
            log(f"  SH01 (sham) mean={m['mean_value']}")
        except Exception as e:
            log(f"  SH01 FAILED: {str(e)[:200]}")

        # ---- hermeticity: all networking blocked at creation --------------
        herm_entry = dict(next(e for e in doc["experiments"] if e["type"] == "reproduce"),
                          experiment_id="HERM", command="bash runner.sh HERM")
        herm_doc = dict(doc, experiments=[herm_entry])
        herm_hash = hashlib.sha256(canonical_json(herm_doc).encode()).hexdigest()
        try:
            m = run_experiment(prereg=herm_doc, prereg_hash=herm_hash,
                               manifest=build_manifest(herm_doc, herm_hash, "HERM",
                                                       budget=budget_spec),
                               hermetic=True, **common)
            hermeticity = ("VERIFIED - network_block_all active, run completed "
                           f"(mean={m['mean_value']})")
        except Exception as e:
            hermeticity = f"NOT ESTABLISHED - {str(e)[:180]}"
        log(f"  hermeticity: {hermeticity}")
        ledger.log_event(run_id, "hermeticity", {"result": hermeticity})

        # ---- P3 verdicts --------------------------------------------------
        rows = p3.judge_run(doc, annex, evidence_root, ledger, run_id)
        sham_rows = p3.judge_run(sham_doc, {"claims": [], "experiments": []},
                                 evidence_root, ledger, run_id)
        (run_dir / "verdicts.json").write_text(json.dumps(
            {"run_id": run_id, "prereg_hash": prereg_hash, "profile": args.profile,
             "verdicts": rows, "sham": sham_rows, "hermeticity": hermeticity,
             "framing": doc["framing"]}, indent=2))
        for r in sham_rows + rows:
            log(f"  {r['experiment_id']} {r['claim_id']} "
                f"observed={r['observed']} -> {r['verdict']}")
    finally:
        telemetry.set_tap(None)
        ledger.bus.emit(run_id, "run.done", {})
        if not args.keep_sandboxes:
            log(f"kill switch: deleted {len(life.kill_all())} sandboxes")
        if s0:
            try:
                adapter.snapshot_delete(s0)
                log(f"snapshot {s0} deleted")
            except Exception as e:
                log(f"snapshot {s0} not deleted: {str(e)[:120]}")
        stream_path = run_dir / "stream.jsonl"
        log(f"recorded {export_stream(ledger, run_id, stream_path)} events -> {stream_path}")
        log(f"replay: repro feed --ledger {RUN_ROOT / 'ledger.db'} "
            f"--run-id {run_id} --replay paced --speed 4")
    return 0


def _sham(prereg: dict, seeds: list[int]) -> tuple[dict, str]:
    """Corrupt a drift-stable claim's target: the sham must fail because the target is
    wrong, not because library drift happened to overlap the corruption."""
    claim = next(c for c in prereg["claims"] if c["id"] in ("C4", "C1"))
    sham_claim = dict(claim, reported_value=round(claim["reported_value"] + SHAM_DELTA, 3))
    entry = {"experiment_id": "SH01", "claim_id": claim["id"], "type": "reproduce",
             "command": "bash runner.sh SH01",
             "rule": {"id": "R-SH01", "kind": "abs_tolerance",
                      "target": sham_claim["reported_value"], "tolerance": 0.01,
                      "aggregate": "mean"}}
    doc = {"version": 1, "role": "sham_twin", "paper": prereg["paper"],
           "claims": [sham_claim], "experiments": [entry],
           "tolerances": {claim["id"]: 0.01}, "seeds": seeds}
    return doc, hashlib.sha256(canonical_json(doc).encode()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())

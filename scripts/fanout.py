"""Run several papers through the autonomous pipeline at once and table the results.

One OS process per paper (scripts/auto_run.py), which is what makes this safe: no
shared Python state, unique run ids, and a shared ledger opened in WAL mode. The
real ceiling is the Daytona org quota - measured at 10 GiB total sandbox memory,
against 4 GiB for a daytona-medium S0 box and 1 GiB for daytona-small - so the
default concurrency of 2 is a quota decision, not a code limit. Creates queue
inside each run rather than failing, so overshooting costs wall-clock, not runs.

Usage:
  python scripts/fanout.py papers/fashion-mnist papers/best-scored-rf
  python scripts/fanout.py --all --concurrency 2 --base-snapshot daytona-small
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPO = Path(__file__).resolve().parent.parent
RUN_ROOT = REPO / "runs" / "auto"
PAPERS = REPO / "papers"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] fanout: {msg}", flush=True)


def discover() -> list[Path]:
    return sorted(d for d in PAPERS.iterdir()
                  if (d / "paper.json").is_file() and (d / "paper-extract.txt").is_file())


def launch(paper_dir: Path, args) -> dict:
    """One pipeline, one process. Its stdout is teed to a per-paper log file."""
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    logfile = RUN_ROOT / f"fanout-{paper_dir.name}-{int(time.time())}.log"
    cmd = [sys.executable, str(REPO / "scripts" / "auto_run.py"), str(paper_dir),
           "--seeds", args.seeds, "--base-snapshot", args.base_snapshot]
    t0 = time.monotonic()
    log(f"start {paper_dir.name} -> {logfile.name}")
    with logfile.open("w") as fh:
        proc = subprocess.run(cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT, text=True)
    elapsed = round(time.monotonic() - t0, 1)
    log(f"done  {paper_dir.name} exit={proc.returncode} in {elapsed}s")
    return {"paper": paper_dir.name, "paper_dir": str(paper_dir), "exit": proc.returncode,
            "seconds": elapsed, "log": str(logfile), **newest_handle(paper_dir)}


def newest_handle(paper_dir: Path) -> dict:
    """Find the run this launch produced: the newest handle naming this paper."""
    best, best_mtime = {}, -1.0
    for handle_path in RUN_ROOT.glob("*/handle.json"):
        try:
            handle = json.loads(handle_path.read_text())
        except (OSError, ValueError):
            continue
        if Path(handle.get("paper_dir", "")).name != paper_dir.name:
            continue
        mtime = handle_path.stat().st_mtime
        if mtime > best_mtime:
            best, best_mtime = handle, mtime
    if not best:
        return {"run_id": None, "run_dir": None}
    return {"run_id": best.get("run_id"), "run_dir": best.get("run_dir"),
            "failed_at": best.get("failed_at")}


def verdict_rows(run_dir: str | None) -> list[dict]:
    if not run_dir:
        return []
    path = Path(run_dir) / "verdicts.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    return [{"experiment": r.get("experiment_id"), "claim": r.get("claim_id"),
             "observed": r.get("observed"), "verdict": r.get("verdict"),
             "degraded": bool(data.get("degraded"))} for r in data.get("verdicts", [])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paper_dirs", nargs="*", help="paper directories; default --all")
    ap.add_argument("--all", action="store_true", help="every paper under papers/")
    ap.add_argument("--concurrency", type=int, default=2,
                    help="pipelines in flight (default 2: the 10 GiB org memory quota "
                         "fits two 4 GiB S0 boxes)")
    ap.add_argument("--seeds", default="17,41,93")
    ap.add_argument("--base-snapshot", default="daytona-medium")
    ap.add_argument("--gc-first", action="store_true",
                    help="reclaim quota from finished runs before launching")
    args = ap.parse_args()

    papers = [Path(p) for p in args.paper_dirs] if args.paper_dirs else []
    if args.all or not papers:
        papers = discover()
    missing = [p for p in papers if not (p / "paper.json").is_file()]
    if missing:
        print(f"not a paper directory: {', '.join(str(m) for m in missing)}", file=sys.stderr)
        return 1

    if args.gc_first:
        log("reclaiming quota first (repro gc)")
        subprocess.run([sys.executable, "-m", "repro.cli", "gc", "--keep-previews", "1"],
                       cwd=REPO, check=False)

    log(f"{len(papers)} papers, concurrency {args.concurrency}, base {args.base_snapshot}")
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        results = list(pool.map(lambda p: launch(p, args), papers))

    summary = {"launched_at": time.time(), "concurrency": args.concurrency,
               "base_snapshot": args.base_snapshot,
               "runs": [{**r, "verdicts": verdict_rows(r.get("run_dir"))} for r in results]}
    out = RUN_ROOT / f"fanout-{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2))

    print("\n=== fan-out results ===")
    for r in summary["runs"]:
        state = "ok" if r["exit"] == 0 else f"exit {r['exit']}"
        print(f"\n{r['paper']}  [{state}, {r['seconds']}s]  run {r['run_id']}")
        if not r["verdicts"]:
            print("  (no verdicts; see the run log)")
        for v in r["verdicts"]:
            print(f"  {v['experiment']:<8} {v['claim']:<6} observed={v['observed']} "
                  f"-> {v['verdict']}")
    print(f"\nsummary written to {out}")
    return 0 if all(r["exit"] == 0 for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())

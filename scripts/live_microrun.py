"""Live micro-run: prove the feed against a real sandbox, then delete it.

The smallest thing that exercises the whole path — a sandbox from a stock snapshot
running a short runner.sh that prints `::progress k/n` per seed, with the tap following
it and the browser reading the stream. Measures how long output takes to travel from
the sandbox to a subscriber, watches progress step per seed, and fires the kill switch.

Deliberately cheap: one small sandbox, a 10 minute TTL, deleted in a finally.

    python scripts/live_microrun.py --seeds 4 --sleep 12
"""

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repro import feed, logtap, telemetry  # noqa: E402
from repro.orchestrator.budget import Budget  # noqa: E402
from repro.orchestrator.daytona_client import DaytonaAdapter  # noqa: E402
from repro.orchestrator.gates import Gates  # noqa: E402
from repro.orchestrator.ledger import Ledger  # noqa: E402
from repro.orchestrator.lifecycle import Lifecycle  # noqa: E402

WORK = "/home/daytona/work"

RUNNER_SH = """#!/bin/bash
# a stand-in experiment: n seeds, each a fixed sleep, reporting progress the way the
# real runner does when the feed asks for it. Each line carries the wall clock at the
# moment it was produced, so delivery latency is measured rather than guessed.
n=$1
sleep_s=$2
marker="$(dirname "$0")/.repro_progress"
for i in $(seq 1 "$n"); do
  echo "[runner] MICRO seed=$i emitted=$(date +%s.%N)"
  sleep "$sleep_s"
  echo "[runner] MICRO seed=$i done emitted=$(date +%s.%N)"
  if [ -f "$marker" ]; then
    echo "::progress $i/$n" >> "$(dirname "$0")/$(cat "$marker")"
  fi
done
echo "[runner] MICRO done emitted=$(date +%s.%N)"
"""

EMITTED = re.compile(r"emitted=(\d+\.\d+)")


def watch(bus, run_id, latencies, progress, stop):
    """Stand in for the browser: timestamp every chunk as it arrives and compare with
    the moment the sandbox produced the line."""
    q = bus.subscribe(run_id)
    try:
        while not stop.is_set():
            try:
                frame = q.get(timeout=0.2)
            except queue.Empty:
                continue
            arrived = time.time()
            if frame["kind"] == "log.chunk":
                for match in EMITTED.finditer(frame["payload"].get("text", "")):
                    latencies.append(round(arrived - float(match.group(1)), 3))
            elif frame["kind"] == "attempt.progress":
                progress.append(frame["payload"])
    finally:
        bus.unsubscribe(run_id, q)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--sleep", type=float, default=12)
    ap.add_argument("--snapshot", default="daytona-small")
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--ttl", type=int, default=10)
    ap.add_argument("--run-dir", default="runs/microrun")
    args = ap.parse_args()

    run_id = f"micro-{int(time.time())}"
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(run_dir / "ledger.db")
    ledger.create_run(run_id, paper_hash="0" * 64, prereg_hash="0" * 64)
    gates = Gates(ledger)
    gates.approve(run_id, "G1", "microrun")
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates,
                     Budget(ledger, run_id, {"sandbox_minutes": 200}), run_id)
    feed.serve_background(run_dir / "ledger.db", run_dir, args.port, bus=ledger.bus,
                          default_run=run_id)
    print(f"feed: http://127.0.0.1:{args.port}/?run_id={run_id}", flush=True)

    # a subscriber standing in for the browser, so latency is measured end to end
    latencies, progress, stop = [], [], threading.Event()
    threading.Thread(target=watch, args=(ledger.bus, run_id, latencies, progress, stop),
                     daemon=True).start()
    report = {"run_id": run_id, "seeds": args.seeds}
    sid = None
    try:
        t0 = time.monotonic()
        sid = life.create("experiment", name=f"micro-{run_id}"[:40],
                          snapshot=args.snapshot, ttl_minutes=args.ttl)
        report["create_seconds"] = round(time.monotonic() - t0, 1)
        print(f"sandbox {sid} in {report['create_seconds']}s", flush=True)

        adapter.exec(sid, f"mkdir -p {WORK}", timeout=60)
        adapter.write_file(sid, f"{WORK}/runner.sh", RUNNER_SH.encode())
        attempt_id = ledger.start_attempt(run_id, "MICRO", "0" * 64, "snapshot",
                                          args.snapshot, "bash runner.sh", [1] * args.seeds,
                                          cost_est=args.ttl)
        ledger.bind_sandbox(attempt_id, sid)

        tap = logtap.start_log_tap(adapter, sid, ledger.bus, run_id, attempt_id,
                                   total_seeds=args.seeds)
        report["tap_started"] = tap is not None
        r = adapter.exec(sid, f"bash runner.sh {args.seeds} {args.sleep} > stdout.log 2>&1",
                         cwd=WORK, timeout=int(args.seeds * args.sleep + 120))
        report["runner_exit"] = r.exit_code
        if tap:
            tap.close()
            report["tap_degraded"] = tap.degraded
        time.sleep(1)
        stop.set()
        time.sleep(0.4)
        report["progress"] = progress
        report["seeds_reported"] = len(progress)
        report["chunk_latencies_s"] = sorted(latencies)
        if latencies:
            ordered = sorted(latencies)
            report["latency_median_s"] = ordered[len(ordered) // 2]
            report["latency_max_s"] = ordered[-1]
        kill_at = time.time()
        ledger.finish_attempt(attempt_id, r.exit_code, None)
        report["kill_requested_at"] = kill_at
    finally:
        kill_t0 = time.time()
        killed = life.kill_all()
        report["killed"] = killed
        report["kill_switch_seconds"] = round(time.time() - kill_t0, 2)
        rows = ledger.events_for(run_id, "kill_switch")
        report["kill_switch_visible_after_s"] = (
            round(rows[-1]["created_at"] - kill_t0, 2) if rows else None)
        ledger.bus.emit(run_id, "run.done", {})
        print(json.dumps(report, indent=2), flush=True)
        (run_dir / f"{run_id}.json").write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

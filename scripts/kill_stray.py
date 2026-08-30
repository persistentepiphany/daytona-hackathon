"""Delete leftover sandboxes, keeping the build sandbox that serves the preview.

A run tears down its own sandboxes; this catches what a crashed or interrupted
run left behind. Sandboxes labeled kind=build are preserved by default because
they host the deployed preview for its demo window.

It also protects runs that are still in flight. With several pipelines running at
once, "leftover" is not something a label can tell you: this sweep deleted a live
archaeology box out from under a build loop, which then failed every remaining
round on 'sandbox not found'. So a sandbox whose run has written to a ledger
within --idle-minutes is treated as live and kept. Pass --idle-minutes 0 to
disable that check and sweep purely on labels.
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repro.orchestrator.daytona_client import make_daytona  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def live_runs(idle_minutes: float, ledgers: list[str] | None = None) -> dict[str, float]:
    """run_id -> minutes since its last ledger write, for runs still writing.

    Read-only and defensive: a ledger being written by another process, or missing
    entirely, must not stop the sweep - it just means fewer runs are known live.
    """
    if idle_minutes <= 0:
        return {}
    paths = [Path(p) for p in ledgers] if ledgers else sorted(REPO.glob("runs/*/ledger.db"))
    cutoff = time.time() - idle_minutes * 60
    live: dict[str, float] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
            rows = db.execute(
                "SELECT run_id, MAX(created_at) AS last FROM events GROUP BY run_id "
                "HAVING last >= ?", (cutoff,)).fetchall()
            db.close()
        except sqlite3.Error:
            continue
        for run_id, last in rows:
            age = (time.time() - last) / 60
            live[run_id] = min(age, live.get(run_id, age))
    return live


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-kind", default="build,build_demo",
                    help="comma-separated 'kind' labels to preserve "
                         "(default: build,build_demo - P5 labels the preview build_demo)")
    ap.add_argument("--keep-run", default="",
                    help="comma-separated run ids to preserve (use for a run still in flight)")
    ap.add_argument("--idle-minutes", type=float, default=15.0,
                    help="keep sandboxes whose run wrote to a ledger this recently "
                         "(default 15; 0 disables the in-flight check)")
    ap.add_argument("--ledger", action="append", default=None,
                    help="ledger to consult for in-flight runs (repeatable; "
                         "default: every runs/*/ledger.db)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    keep_kinds = {k.strip() for k in args.keep_kind.split(",") if k.strip()}
    keep_runs = {r.strip() for r in args.keep_run.split(",") if r.strip()}
    in_flight = live_runs(args.idle_minutes, args.ledger)
    if in_flight:
        print("in flight (kept): " + ", ".join(
            f"{r} [{m:.1f}m idle]" for r, m in sorted(in_flight.items())))
    daytona = make_daytona()
    kept, killed = [], []
    for sb in daytona.list():
        labels = getattr(sb, "labels", None) or {}
        if labels.get("kind") in keep_kinds or labels.get("run") in keep_runs:
            kept.append((sb.id, labels))
            continue
        if labels.get("run") in in_flight:
            kept.append((sb.id, {**labels, "_why": "run still writing to its ledger"}))
            continue
        if args.dry_run:
            killed.append((sb.id, labels, "dry-run"))
            continue
        try:
            sb.delete()
            killed.append((sb.id, labels, "deleted"))
        except Exception as e:  # noqa: BLE001 - report and continue
            killed.append((sb.id, labels, f"error: {str(e)[:120]}"))

    for sid, labels in kept:
        print(f"kept    {sid[:12]} {labels}")
    for sid, labels, how in killed:
        print(f"{how:<8}{sid[:12]} {labels}")
    print(f"\n{len(kept)} kept, {len(killed)} targeted")
    return 0


if __name__ == "__main__":
    sys.exit(main())

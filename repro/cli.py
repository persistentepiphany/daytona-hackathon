"""Command-line entry point: kill switch, replay resolution, report, thin build.

The stage drivers live in scripts/ (day0_check.py, run_calibration_p1.py,
run_calibration_p2.py); this CLI covers the operations that act on an existing
run's ledger and artifacts.
"""

import argparse
import json
import sys
from pathlib import Path


def cmd_kill(args) -> int:
    from .orchestrator.budget import Budget
    from .orchestrator.daytona_client import DaytonaAdapter
    from .orchestrator.gates import Gates
    from .orchestrator.ledger import Ledger
    from .orchestrator.lifecycle import Lifecycle

    ledger = Ledger(args.ledger)
    life = Lifecycle(DaytonaAdapter(), ledger, Gates(ledger),
                     Budget(ledger, args.run_id, {}), args.run_id)
    deleted = life.kill_all()
    print(f"deleted {len(deleted)} sandboxes for run {args.run_id}")
    return 0


def cmd_replay(args) -> int:
    from .orchestrator.ledger import Ledger

    print(json.dumps(Ledger(args.ledger).resolve_replay(args.attempt), indent=2))
    return 0


def cmd_report(args) -> int:
    from .orchestrator.ledger import Ledger
    from .pipeline.report import report_from_files

    ledger = Ledger(Path(args.run_dir).parent / "ledger.db")
    text = report_from_files(args.run_dir, ledger, args.title)
    out = Path(args.run_dir) / "report.md"
    out.write_text(text)
    print(text)
    print(f"written to {out}", file=sys.stderr)
    return 0


def cmd_build(args) -> int:
    from .orchestrator.budget import Budget
    from .orchestrator.daytona_client import DaytonaAdapter
    from .orchestrator.gates import Gates
    from .orchestrator.ledger import Ledger
    from .orchestrator.lifecycle import Lifecycle
    from .pipeline.p5_build import deploy, fallback_app_files

    run_dir = Path(args.run_dir)
    verdicts = json.loads((run_dir / "verdicts.json").read_text())
    run_id = verdicts["run_id"]
    ledger = Ledger(run_dir.parent / "ledger.db")
    gates = Gates(ledger)
    life = Lifecycle(DaytonaAdapter(), ledger, gates,
                     Budget(ledger, run_id, {"sandbox_minutes": 4000}), run_id)
    rows = verdicts["sham"] + verdicts["verdicts"]
    files = fallback_app_files(rows, verdicts["hermeticity"], args.title)
    result = deploy(life, life.adapter, ledger, run_id, files)
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="repro")
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("kill", help="kill switch: delete every sandbox labeled with the run")
    k.add_argument("--ledger", required=True)
    k.add_argument("--run-id", required=True)
    k.set_defaults(fn=cmd_kill)

    r = sub.add_parser("replay", help="resolve everything needed to re-execute an attempt")
    r.add_argument("--ledger", required=True)
    r.add_argument("--attempt", required=True)
    r.set_defaults(fn=cmd_replay)

    p = sub.add_parser("report", help="render the verdict report from persisted artifacts")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--title", default="calibration run")
    p.set_defaults(fn=cmd_report)

    b = sub.add_parser("build", help="deploy the thin what-survived app to a sandbox")
    b.add_argument("--run-dir", required=True)
    b.add_argument("--title", default="calibration run")
    b.set_defaults(fn=cmd_build)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

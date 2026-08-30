"""Drive scripted agent actions through the real choke point, with the feed watching.

No LLM, no sandbox, no network: an in-memory adapter behind a real ArchaeologySession,
with every action going through `orchestrator.actions.apply_action` exactly as an
Implementer proposal would. What appears in the browser is therefore produced by the
same code path a real run uses, which is the only version of this demo worth showing.

    python scripts/feed_driver.py --port 8700
    open http://127.0.0.1:8700/?run_id=<the run id it prints>
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repro import feed, telemetry  # noqa: E402
from repro.orchestrator.actions import apply_action  # noqa: E402
from repro.orchestrator.budget import Budget  # noqa: E402
from repro.orchestrator.gates import Gates  # noqa: E402
from repro.orchestrator.ledger import Ledger  # noqa: E402
from repro.orchestrator.lifecycle import Lifecycle  # noqa: E402
from repro.pipeline.p1_archaeology import ArchaeologySession  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
from tests.fake_adapter import FakeAdapter  # noqa: E402

TRAIN_V1 = '''"""Fit one model for one seed and report test accuracy."""

def main(seed):
    model = fit(seed)
    return accuracy(model)
'''

TRAIN_V2 = '''"""Fit one model for one seed and report test accuracy."""

def main(seed, scale=255.0):
    """The paper does not state whether pixels are scaled; take the conventional
    choice and record it as an ambiguity."""
    model = fit(seed, scale=scale)
    return accuracy(model)
'''

ACTIONS = [
    {"action": "run", "cmd": "python3 -m venv venv"},
    {"action": "run", "cmd": "venv/bin/pip install -q numpy scikit-learn"},
    {"action": "write", "path": "train.py", "content": TRAIN_V1},
    {"action": "write", "path": "smoke.sh",
     "content": "set -e\nvenv/bin/python -c 'import sklearn'\n"},
    {"action": "run", "cmd": "bash smoke.sh"},
    # a failure, because a feed that only ever shows success is not worth watching
    {"action": "run", "cmd": "venv/bin/python train.py --seed 17", "check": False},
    {"action": "write", "path": "train.py", "content": TRAIN_V2},
    {"action": "run", "cmd": "venv/bin/python train.py --seed 17"},
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--pause", type=float, default=1.5, help="seconds between actions")
    ap.add_argument("--run-dir", default="runs/feed-driver")
    args = ap.parse_args()

    run_id = f"drv-{int(time.time())}"
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(run_dir / "ledger.db")
    ledger.create_run(run_id, paper_hash="0" * 64, prereg_hash="0" * 64)

    gates = Gates(ledger)
    budget = Budget(ledger, run_id, {"sandbox_minutes": 600})
    adapter = FakeAdapter()
    fail = type("R", (), {"exit_code": 1, "output":
                          "Traceback (most recent call last):\n"
                          "  File 'train.py', line 4, in main\n"
                          "TypeError: fit() got an unexpected keyword 'scale'\n"})()
    ok = type("R", (), {"exit_code": 0, "output":
                        '{"claim": "C1", "seed": 17, "metric": "test_accuracy", '
                        '"value": 0.811}\n'})()
    adapter.exec_responses = {"train.py --seed": [fail, ok]}
    life = Lifecycle(adapter, ledger, gates, budget, run_id)

    server = feed.serve_background(run_dir / "ledger.db", run_dir, args.port,
                                   bus=ledger.bus, default_run=run_id)
    print(f"feed: http://127.0.0.1:{args.port}/?run_id={run_id}", flush=True)
    print("(open it now; actions start in 3s)", flush=True)
    time.sleep(3)

    gates.approve(run_id, "G1", "feed-driver")
    session = ArchaeologySession(life, adapter, ledger, run_id, base_snapshot="base")
    telemetry.set_tap(telemetry.ActionTap(ledger.bus, run_id, role="implementer",
                                          evidence_root=run_dir / run_id))
    try:
        for action in ACTIONS:
            apply_action(session, action)
            print(f"  applied {action['action']}: "
                  f"{action.get('cmd') or action.get('path')}", flush=True)
            time.sleep(args.pause)
    finally:
        telemetry.set_tap(None)
    ledger.bus.emit(run_id, "run.done", {"actions": len(ACTIONS)})
    print(f"done: {len(ledger.events_for(run_id))} events for {run_id}", flush=True)
    print("feed still serving; Ctrl-C to stop", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

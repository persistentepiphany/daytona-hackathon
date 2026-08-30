"""Live-API integration suite, mirroring the T3/T4 acceptance criteria against a
real account. Never runs by default: gate with DAYTONA_LIVE=1 and run locally.

    DAYTONA_LIVE=1 DAYTONA_API_KEY=... pytest tests/test_live_integration.py -v
"""

import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DAYTONA_LIVE") != "1",
    reason="live integration is opt-in: set DAYTONA_LIVE=1 and run locally",
)

RUN = f"live-{int(time.time())}"
BASE = "daytona-small"


@pytest.fixture(scope="module")
def stack():
    from repro.orchestrator.budget import Budget
    from repro.orchestrator.daytona_client import DaytonaAdapter
    from repro.orchestrator.gates import Gates
    from repro.orchestrator.ledger import Ledger
    from repro.orchestrator.lifecycle import Lifecycle

    ledger = Ledger(f"/tmp/live-{RUN}.db")
    ledger.create_run(RUN, paper_hash="0" * 64, prereg_hash="0" * 64)
    gates = Gates(ledger)
    gates.approve(RUN, "G1", "live-suite")
    adapter = DaytonaAdapter()
    life = Lifecycle(adapter, ledger, gates, Budget(ledger, RUN, {"sandbox_minutes": 500}), RUN)
    yield adapter, ledger, life
    life.kill_all()


def test_t3_archaeology_freeze_and_boot(stack):
    """Marker written before freeze must survive a fresh boot from the frozen
    snapshot (the S0 linchpin)."""
    adapter, ledger, life = stack
    from repro.pipeline.p1_archaeology import ArchaeologySession

    session = ArchaeologySession(life, adapter, ledger, RUN, base_snapshot=BASE,
                                 ttl_minutes=30)
    snap = f"live-s0-{RUN}"
    try:
        session.put_file("smoke.sh", "test -f marker.txt\n")
        session.sh("echo live-marker > marker.txt")
        session.smoke()
        session.freeze(snap)
        session.verify_s0_boot(snap)
    finally:
        session.teardown()
        try:
            adapter.snapshot_delete(snap)
        except Exception:
            pass


def test_t4_tarball_delivery_and_exec(stack):
    """Candidate tarball lands at its pinned SHA and extracts runnable."""
    adapter, ledger, life = stack
    from repro.pipeline.p2_experiments import deliver_candidate

    sid = life.create("experiment", name=f"tarball-{RUN}"[:40], snapshot=BASE,
                      ttl_minutes=20)
    try:
        sha = deliver_candidate(adapter, sid, {"hello.py": "print('tarball-ok')\n"},
                                work="/home/daytona/work")
        assert len(sha) == 64
        r = adapter.exec(sid, "python3 hello.py", cwd="/home/daytona/work", timeout=60)
        assert r.exit_code == 0 and "tarball-ok" in r.output
    finally:
        life.delete(sid)

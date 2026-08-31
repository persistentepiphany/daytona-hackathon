"""Concurrent pipelines: cross-process ledger, quota-aware creates, quota GC.

The failure these cover is the one that showed up when several papers ran at
once: colliding run ids, a shared SQLite file without WAL, a sandbox create that
died on a quota refusal instead of queueing, and quota held forever by finished
runs.
"""

import multiprocessing as mp
import re

import pytest

from repro.orchestrator import gc as gcmod
from repro.orchestrator.budget import Budget
from repro.orchestrator.gates import Gates
from repro.orchestrator.ledger import Ledger
from repro.orchestrator.lifecycle import POLICIES, Lifecycle, is_quota_error
from tests.fake_adapter import FakeAdapter

RUN = "run-c"


@pytest.fixture
def stack(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    gates = Gates(ledger)
    gates.approve(RUN, "G1", "test")
    budget = Budget(ledger, RUN, {"sandbox_minutes": 10000})
    adapter = FakeAdapter()
    adapter.snapshots.add("s0")
    return ledger, Lifecycle(adapter, ledger, gates, budget, RUN)


def test_ledger_is_wal_so_processes_can_share_it(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    mode = ledger.db.execute("PRAGMA journal_mode").fetchone()[0]
    timeout = ledger.db.execute("PRAGMA busy_timeout").fetchone()[0]
    assert mode.lower() == "wal"
    assert timeout >= 5000


def _writer(path, run_id, n):  # pragma: no cover - runs in a child process
    led = Ledger(path)
    led.create_run(run_id, paper_hash="p" * 64, prereg_hash="h" * 64)
    for i in range(n):
        led.log_event(run_id, "archaeology_cmd", {"i": i})
    led.close()


def test_two_processes_write_the_same_ledger(tmp_path):
    path = str(tmp_path / "ledger.db")
    Ledger(path).close()  # create the file and set WAL before the fan-out
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(path, f"run-{i}", 40)) for i in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert [p.exitcode for p in procs] == [0, 0, 0]
    led = Ledger(path)
    for i in range(3):
        assert len(led.events_for(f"run-{i}", "archaeology_cmd")) == 40


def test_budget_charge_is_serialized_across_threads(tmp_path):
    """The race that forced P2 to one worker: Budget wrote outside the ledger lock."""
    import concurrent.futures as cf

    ledger = Ledger(tmp_path / "ledger.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    budget = Budget(ledger, RUN, {"sandbox_minutes": 1000})
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: budget.charge("sandbox_minutes", 1, note=f"n{i}"), range(100)))
    assert budget.spent("sandbox_minutes") == 100


def test_quota_refusal_queues_then_succeeds(stack, monkeypatch):
    ledger, life = stack
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}
    real_create = life.create

    def flaky(kind, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Total memory quota exceeded for organization")
        return real_create(kind, **kwargs)

    monkeypatch.setattr(life, "create", flaky)
    sid = life.create_with_retry("experiment", name="e1", snapshot="s0", wait_seconds=0)
    assert sid and calls["n"] == 3
    retries = ledger.events_for(RUN, "sandbox_create_retry")
    assert len(retries) == 2 and all('"quota": true' in r["payload"] for r in retries)


def test_non_quota_failure_raises_immediately(stack, monkeypatch):
    ledger, life = stack

    def broken(kind, **kwargs):
        raise RuntimeError("snapshot s0-missing not found")

    monkeypatch.setattr(life, "create", broken)
    with pytest.raises(RuntimeError, match="not found"):
        life.create_with_retry("experiment", name="e1", snapshot="s0-missing", wait_seconds=0)
    assert len(ledger.events_for(RUN, "sandbox_create_retry")) == 1


@pytest.mark.parametrize("msg,expected", [
    ("Total memory quota exceeded", True),
    ("sandbox limit reached for organization", True),
    ("insufficient capacity on runner", True),
    ("snapshot not found", False),
    ("connection reset by peer", False),
])
def test_quota_error_detection(msg, expected):
    assert is_quota_error(RuntimeError(msg)) is expected


def test_run_ids_are_unique_within_a_second():
    """Two pipelines launched together must not share a run id."""
    import time
    import uuid

    ids = {f"auto-{int(time.time())}-{uuid.uuid4().hex[:6]}" for _ in range(200)}
    assert len(ids) == 200
    assert all(re.fullmatch(r"auto-\d+-[0-9a-f]{6}", i) for i in ids)


# --- quota GC ---------------------------------------------------------------

SNAPSHOTS = [
    {"name": "s0-auto-300", "size_gb": 14.5, "created_at": "2026-08-30T15:13:13.971Z"},
    {"name": "s0-auto-200", "size_gb": 14.5, "created_at": "2026-08-30T14:26:12.572Z"},
    {"name": "s0-auto-100", "size_gb": 14.5, "created_at": "2026-08-30T13:05:09.872Z"},
    {"name": "daytona-medium", "size_gb": 6.8, "created_at": "2026-07-28T14:58:11.540Z"},
]
SANDBOXES = [
    {"id": "a" * 12, "labels": {"kind": "build_demo", "run": "auto-300"}, "memory_gib": 1,
     "disk_gib": 3, "created_at": "2026-08-30T14:51:52.832Z"},
    {"id": "b" * 12, "labels": {"kind": "build_demo", "run": "auto-200"}, "memory_gib": 1,
     "disk_gib": 3, "created_at": "2026-08-30T14:26:12.572Z"},
    {"id": "c" * 12, "labels": {"kind": "experiment", "run": "auto-400"}, "memory_gib": 4,
     "disk_gib": 8, "created_at": "2026-08-30T15:20:00.000Z"},
]


def test_gc_keeps_provider_images_and_the_newest_s0():
    delete, keep = gcmod.plan_snapshots(SNAPSHOTS, keep_runs=set(), keep_newest=1)
    assert [s["name"] for s in delete] == ["s0-auto-200", "s0-auto-100"]
    assert [s["name"] for s in keep] == ["s0-auto-300"]  # base images are never ours


def test_gc_never_touches_a_pinned_run():
    delete, keep = gcmod.plan_snapshots(SNAPSHOTS, keep_runs={"auto-100"}, keep_newest=1)
    assert [s["name"] for s in delete] == ["s0-auto-200"]
    assert {s["name"] for s in keep} == {"s0-auto-300", "s0-auto-100"}


def test_gc_reaps_stale_previews_but_keeps_the_live_demo_and_running_work():
    delete, keep = gcmod.plan_sandboxes(SANDBOXES, keep_runs=set(), keep_newest_demo=1)
    assert [b["id"] for b in delete] == ["b" * 12]
    assert [b["id"] for b in keep] == ["a" * 12]  # experiment boxes are not the GC's business


def test_gc_reports_what_it_reclaims():
    snap_del, _ = gcmod.plan_snapshots(SNAPSHOTS, keep_runs=set(), keep_newest=1)
    box_del, _ = gcmod.plan_sandboxes(SANDBOXES, keep_runs=set(), keep_newest_demo=1)
    freed = gcmod.reclaimed(snap_del, box_del)
    assert freed == {"snapshots": 2, "snapshot_storage_gb": 29.0, "sandboxes": 1,
                     "memory_gib": 1, "disk_gib": 3}


def test_gc_respects_a_minimum_age():
    delete, _ = gcmod.plan_snapshots(SNAPSHOTS, keep_runs=set(), keep_newest=0,
                                     min_age_hours=24 * 3650)
    assert delete == []


def test_every_sandbox_create_goes_through_the_retry():
    """A create that skips create_with_retry dies on a quota refusal instead of
    queueing. verify_s0_boot was exactly that: it survived the first pass of this
    work and killed a live run right after its smoke gate passed."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    bare = re.compile(r"\b(?:life|lifecycle|self\.lifecycle)\.create\(")
    offenders = []
    for path in list((root / "repro").rglob("*.py")) + list((root / "scripts").rglob("*.py")):
        if path.name == "lifecycle.py":  # the definition and its own retry wrapper
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if bare.search(line):
                offenders.append(f"{path.relative_to(root)}:{n}")
    assert not offenders, "bare lifecycle.create() calls: " + ", ".join(offenders)


def test_kill_stray_treats_a_run_still_writing_as_live(tmp_path):
    """The sweep deleted a live archaeology box mid-build once; a run that is
    still writing to its ledger must read as in flight, not as leftover."""
    import importlib.util
    import time as _time
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "kill_stray", Path(__file__).resolve().parent.parent / "scripts" / "kill_stray.py")
    kill_stray = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kill_stray)

    path = tmp_path / "ledger.db"
    led = Ledger(path)
    for rid in ("run-live", "run-old"):
        led.create_run(rid, paper_hash="p" * 64, prereg_hash="h" * 64)
    led.log_event("run-live", "archaeology_cmd", {"cmd": "pip install"})
    led.log_event("run-old", "archaeology_cmd", {"cmd": "pip install"})
    # backdate one run past the idle window
    led.db.execute("UPDATE events SET created_at=? WHERE run_id='run-old'",
                   (_time.time() - 3600,))
    led.db.commit()

    live = kill_stray.live_runs(15.0, [str(path)])
    assert set(live) == {"run-live"}
    assert kill_stray.live_runs(0, [str(path)]) == {}  # opt out sweeps on labels alone
    assert kill_stray.live_runs(15.0, [str(tmp_path / "missing.db")]) == {}


@pytest.mark.parametrize("text,lost", [
    ('Failed to upload files: 404: {"message":"not found: sandbox c70b6369 not found"}', True),
    ("sandbox is not running", True),
    ("smoke.sh: line 3: python: command not found", False),
    ("ModuleNotFoundError: No module named 'numpy'", False),
])
def test_a_vanished_sandbox_is_not_a_build_failure(text, lost):
    from repro.auto.build import is_environment_lost

    assert is_environment_lost(text) is lost


def test_a_refused_create_leaves_no_charge(stack, monkeypatch):
    """20 quota retries used to charge the archaeology TTL 20 times and trip the
    run's own budget ceiling before a single sandbox existed."""
    ledger, life = stack
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}
    real_create = life.adapter.create

    def flaky(spec):
        calls["n"] += 1
        if calls["n"] < 4:
            raise RuntimeError("Total memory limit exceeded. Maximum allowed: 10GiB.")
        return real_create(spec)

    monkeypatch.setattr(life.adapter, "create", flaky)
    before = life.budget.spent("sandbox_minutes")
    life.create_with_retry("experiment", name="e1", snapshot="s0", wait_seconds=0)
    # one sandbox exists, so exactly one experiment TTL is charged
    assert life.budget.spent("sandbox_minutes") - before == POLICIES["experiment"].default_ttl


def test_the_ceiling_still_blocks_a_create_before_it_happens(tmp_path):
    from repro.orchestrator.budget import BudgetExceeded

    ledger = Ledger(tmp_path / "ledger.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    gates = Gates(ledger)
    gates.approve(RUN, "G1", "test")
    adapter = FakeAdapter()
    adapter.snapshots.add("s0")
    life = Lifecycle(adapter, ledger, gates, Budget(ledger, RUN, {"sandbox_minutes": 10}), RUN)
    with pytest.raises(BudgetExceeded):
        life.create("experiment", name="e1", snapshot="s0")
    assert not adapter.sandboxes  # refused before the provider was ever called

"""Orchestrator spine: ledger replayability, gate enforcement, lifecycle, kill switch."""

import pytest

from repro.orchestrator.budget import Budget, BudgetExceeded
from repro.orchestrator.gates import GateError, Gates
from repro.orchestrator.ledger import Ledger, LedgerError
from repro.orchestrator.lifecycle import Lifecycle
from tests.fake_adapter import FakeAdapter

RUN = "run-1"


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    led.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    return led


@pytest.fixture
def stack(ledger):
    adapter = FakeAdapter()
    gates = Gates(ledger)
    budget = Budget(ledger, RUN, {"sandbox_minutes": 1000})
    life = Lifecycle(adapter, ledger, gates, budget, RUN)
    return adapter, gates, budget, life, ledger


def test_freeze_is_immutable(ledger):
    ledger.set_run_freeze(RUN, "s0-snap", "abc123", "r" * 64)
    with pytest.raises(LedgerError, match="immutable"):
        ledger.set_run_freeze(RUN, "s0-other", "def", "x")


def test_attempt_finalized_once(ledger):
    att = ledger.start_attempt(RUN, "E001", "m" * 64, "snapshot", "s0-snap", "bash runner.sh E001", [17, 41])
    ledger.finish_attempt(att, 0, "e" * 64)
    with pytest.raises(LedgerError, match="finalized"):
        ledger.finish_attempt(att, 1, None)


def test_replay_resolves_without_agent_memory(ledger):
    ledger.set_run_freeze(RUN, "s0-snap", "abc123", "r" * 64)
    ledger.record_dataset(RUN, "uci/sonar.csv", "d" * 64)
    att = ledger.start_attempt(RUN, "E002", "m" * 64, "snapshot", "s0-snap",
                               "bash runner.sh E002", [17, 41, 93], claim_id="C2")
    replay = ledger.resolve_replay(att)
    assert replay["s0_snapshot"] == "s0-snap"
    assert replay["manifest_hash"] == "m" * 64
    assert replay["seeds"] == [17, 41, 93]
    assert replay["dataset_hashes"] == {"uci/sonar.csv": "d" * 64}
    assert replay["cmd"] == "bash runner.sh E002"


def test_no_sandbox_before_g1(stack):
    adapter, gates, budget, life, ledger = stack
    with pytest.raises(GateError, match="G1"):
        life.create("experiment", name="e1", snapshot="base")
    assert adapter.sandboxes == {}


def test_gate_order_and_single_approval(stack):
    _, gates, _, _, _ = stack
    with pytest.raises(GateError):
        gates.approve(RUN, "G2", "user")
    gates.approve(RUN, "G1", "user")
    with pytest.raises(GateError, match="already"):
        gates.approve(RUN, "G1", "user")
    gates.approve(RUN, "G2", "user")
    assert gates.passed(RUN, "G2")


def test_gpu_requires_g2(stack):
    adapter, gates, _, life, _ = stack
    gates.approve(RUN, "G1", "user")
    with pytest.raises(GateError, match="G2"):
        life.create("gpu", name="g1", image="python:3.11-slim")


def test_lifecycle_policies_applied(stack):
    adapter, gates, _, life, _ = stack
    gates.approve(RUN, "G1", "user")
    sid = life.create("experiment", name="e1", snapshot="base", exp_id="E001")
    spec = adapter.sandboxes[sid]["spec"]
    assert spec.auto_delete_interval == 0  # delete on stop
    assert spec.labels == {"run": RUN, "kind": "experiment", "exp": "E001"}
    assert spec.ttl_minutes == 120
    life.stop(sid)  # auto-delete on stop
    assert sid not in adapter.sandboxes


def test_kill_switch_children_first(stack):
    adapter, gates, _, life, _ = stack
    gates.approve(RUN, "G1", "user")
    parent = life.create("archaeology", name="arch", snapshot="base")
    child1 = life.fork(parent, "c1")
    child2 = life.fork(parent, "c2")
    deleted = life.kill_all()
    assert adapter.sandboxes == {}
    assert deleted.index(child1) < deleted.index(parent)
    assert deleted.index(child2) < deleted.index(parent)


def test_budget_ceiling(stack):
    adapter, gates, budget, life, _ = stack
    gates.approve(RUN, "G1", "user")
    life.create("experiment", name="e1", snapshot="base", ttl_minutes=900)
    with pytest.raises(BudgetExceeded):
        life.create("experiment", name="e2", snapshot="base", ttl_minutes=200)
    assert len(adapter.sandboxes) == 1  # second create never reached the provider

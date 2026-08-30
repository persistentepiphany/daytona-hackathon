"""Acceptance tests for the v2 deltas: condition claims, typed ambiguities, MC
tolerance, action choke point, search-on-failure, tarball delivery, synthetic
mode, ledger-only rerun, implementer convergence, verifier boundary, dashboard
queries, intake gates, demo preview lifecycle, gated push."""

import json
import sqlite3
from pathlib import Path

import pytest

from repro.orchestrator.actions import ActionError, apply_action, validate_action
from repro.orchestrator.budget import Budget
from repro.orchestrator.gates import Gates
from repro.orchestrator.ledger import Ledger
from repro.orchestrator.lifecycle import Lifecycle
from repro.orchestrator.manifest import ManifestError, build_manifest, validate_manifest
from repro.orchestrator.policy import load_policy, parallel_stages
from repro.orchestrator.prereg import PreregError, build_mc_rule, sha256_of
from repro.orchestrator.schemas import SchemaError, normalize_claim, validate_ambiguity
from repro.pipeline import p3_verdict as p3
from repro.pipeline.p0_intake import (IntakeDeclined, evaluate_code_existence,
                                      intake_decision)
from repro.pipeline.p2_experiments import (candidate_tarball, deliver_candidate,
                                           reconstruct_attempt, run_experiment)
from repro.pipeline.p5_build import PushNotApproved, deploy, fallback_app_files, push_output
from repro.roles import implementer
from tests.fake_adapter import FakeAdapter
from repro.orchestrator.adapter import ExecResult

RUN = "run-v2"


@pytest.fixture
def stack(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    gates = Gates(ledger)
    gates.approve(RUN, "G1", "user")
    adapter = FakeAdapter()
    life = Lifecycle(adapter, ledger, gates, Budget(ledger, RUN, {"sandbox_minutes": 10000}), RUN)
    return adapter, ledger, gates, life


# D1 / D2 -------------------------------------------------------------------

def test_condition_object_and_legacy_normalization():
    modern = normalize_claim({"id": "C1", "metric": "rmse", "reported_value": 1.2,
                              "source_loc": "T2",
                              "condition": {"n": 100, "contamination": 0.1,
                                            "distribution": "normal", "replications": 1000}})
    assert modern["condition"]["contamination"] == 0.1
    legacy = normalize_claim({"id": "C2", "metric": "acc", "reported_value": 0.9,
                              "source_loc": "T1", "dataset": "d", "split": "test"})
    assert legacy["condition"] == {"dataset": "d", "split": "test"}
    with pytest.raises(SchemaError, match="condition"):
        normalize_claim({"id": "C3", "metric": "acc", "reported_value": 0.9, "source_loc": "x"})


def test_typed_ambiguities():
    entry = validate_ambiguity({"id": "A1", "question": "q", "config_key": "k",
                                "type": "equation_ambiguity"})
    assert entry["type"] == "equation_ambiguity"
    assert validate_ambiguity({"id": "A2", "question": "q", "config_key": "k"})["type"] == "unstated_choice"
    with pytest.raises(SchemaError, match="type"):
        validate_ambiguity({"id": "A3", "question": "q", "config_key": "k", "type": "vibes"})


# D5 ------------------------------------------------------------------------

def test_mc_tolerance_rule():
    rule = build_mc_rule("R1", target=0.42, reported_se=0.004, replications=1000)
    assert rule["tolerance"] == pytest.approx(0.012)
    assert rule["match"] == "distribution" and rule["min_replications"] == 1000
    with pytest.raises(PreregError):
        build_mc_rule("R2", 0.4, reported_se=0, replications=1000)
    entry = {"experiment_id": "E1", "claim_id": "C1", "type": "reproduce", "rule": rule}
    ok = p3.judge_experiment(entry, {"mean_value": 0.43, "n_seeds": 1000})
    assert ok["verdict"] == p3.REPRODUCED
    thin = p3.judge_experiment(entry, {"mean_value": 0.43, "n_seeds": 10})
    assert thin["verdict"] == p3.INCONCLUSIVE  # below the paper's replication count


# D8 ------------------------------------------------------------------------

def test_action_choke_point_rejects_malformed():
    validate_action({"action": "run", "cmd": "make"})
    validate_action({"action": "write", "path": "a.py", "content": "x = 1"})
    validate_action({"action": "search", "objective": "o", "queries": ["q"]})
    for bad in ({"action": "clone", "repo": "x"}, {"action": "run"},
                {"action": "write", "path": "a"}, {"action": "search", "objective": "o", "queries": "q"},
                "run make", {"action": "run", "cmd": ""}):
        with pytest.raises(ActionError):
            validate_action(bad)


def test_search_action_requires_parallel_client():
    with pytest.raises(ActionError, match="no Parallel"):
        apply_action(None, {"action": "search", "objective": "o", "queries": ["q"]})


def test_implementer_actions_route_through_choke_point():
    proposal = {"commands": ["echo hi"], "files": {"a.py": "x = 1"}, "notes": ""}
    actions = implementer.to_actions(proposal)
    assert [a["action"] for a in actions] == ["write", "run"]
    for a in actions:
        validate_action(a)


# T3: search-on-failure recovery scenario -----------------------------------

def test_archaeology_recovery_reaches_s0(stack):
    adapter, ledger, gates, life = stack
    from repro.pipeline.p1_archaeology import ArchaeologySession, run_with_recovery

    class OneSearchParallel:
        def __init__(self):
            self.searches = []

        def search(self, stage, objective, queries, max_results=5):
            self.searches.append((stage, objective))
            return [{"url": "https://example.invalid/fix", "title": "fix"}]

    session = ArchaeologySession(life, adapter, ledger, RUN, base_snapshot="base")
    adapter.exec_responses["apt-get install libfoo"] = [
        ExecResult(1, "E: unable to locate libfoo"),
        ExecResult(1, "E: unable to locate libfoo"),
        ExecResult(0, "installed"),
    ]
    parallel = OneSearchParallel()
    result = run_with_recovery(session, "apt-get install libfoo", parallel)
    assert result.exit_code == 0
    assert len(parallel.searches) == 1 and parallel.searches[0][0] == "archaeology"
    session.smoke()
    session.freeze("s0-v2")
    assert adapter.snapshot_exists("s0-v2")
    assert ledger.run(RUN)["s0_snapshot"] == "s0-v2"
    kinds = [k for k, _ in adapter.calls]
    assert "create_snapshot" in kinds


# D7 / D6 / T4 ---------------------------------------------------------------

PREREG = {
    "version": 1,
    "paper": {"paper_id": "x", "pdf_sha256": "p" * 64},
    "claims": [{"id": "C1", "metric": "m", "reported_value": 0.5, "source_loc": "T1",
                "condition": {"n": 100, "distribution": "normal", "replications": 5}}],
    "experiments": [{"experiment_id": "E001", "claim_id": "C1", "type": "reproduce",
                     "condition": {"n": 100, "distribution": "normal", "replications": 5},
                     "command": "bash runner.sh E001",
                     "rule": {"id": "R1", "kind": "abs_tolerance", "target": 0.5,
                              "tolerance": 0.02, "aggregate": "mean"}}],
    "tolerances": {"C1": 0.02},
    "seeds": [1, 2, 3, 4, 5],
}


def test_tarball_is_deterministic_and_verified(stack):
    adapter, ledger, gates, life = stack
    files = {"candidate.py": "print('hi')", "cfg.json": "{}"}
    data1, sha1 = candidate_tarball(files)
    data2, sha2 = candidate_tarball(dict(reversed(list(files.items()))))
    assert sha1 == sha2  # order-independent, metadata zeroed
    sid = life.create("experiment", name="t", snapshot="base")
    sha = deliver_candidate(adapter, sid, files)
    assert sha == sha1
    assert any(k == "write_file" and f"candidate-{sha[:12]}" in a[1]
               for k, a in adapter.calls)


def test_synthetic_mode_skips_staging_and_checksums(stack, tmp_path):
    adapter, ledger, gates, life = stack
    from repro.pipeline.staging import stage_datasets

    hashes = stage_datasets(life, adapter, ledger, RUN, "base", "vol", {"f": "u"},
                            "sub", data_mode="synthetic")
    assert hashes == {} and adapter.sandboxes == {}  # true no-op

    h = sha256_of(PREREG)
    manifest = build_manifest(PREREG, h, "E001")
    adapter.exec_responses["runner.sh"] = ExecResult(0, "")
    # runner outputs are pre-seeded as sandbox files the executor pulls back
    metrics = {"experiment_id": "E001", "claim_id": "C1", "type": "reproduce",
               "metric": "m", "rows": [], "mean_value": 0.51, "min_value": 0.5,
               "max_value": 0.52, "n_seeds": 5}

    real_create = adapter.create

    def create_and_seed(spec):
        sid = real_create(spec)
        adapter.files[(sid, "/home/daytona/work/metrics.json")] = json.dumps(metrics).encode()
        adapter.files[(sid, "/home/daytona/work/stdout.log")] = b"ok"
        adapter.files[(sid, "/home/daytona/work/leakage.json")] = b"{}"
        return sid

    adapter.create = create_and_seed
    out = run_experiment(life, adapter, ledger, RUN, PREREG, h, manifest, "base",
                        dataset_hashes={}, evidence_root=tmp_path / "ev",
                        data_mode="synthetic")
    assert out["mean_value"] == 0.51
    assert not any("sha256sum -c" in cmd for _, cmd in adapter.exec_log)

    att = ledger.attempts_for(RUN, "E001")[0]["attempt_id"]
    replay = reconstruct_attempt(ledger, att)
    assert replay["manifest"]["experiment_id"] == "E001"
    assert replay["data_mode"] == "synthetic"
    assert replay["manifest"]["condition"]["n"] == 100


def test_manifest_condition_is_gated():
    h = sha256_of(PREREG)
    m = build_manifest(PREREG, h, "E001")
    assert validate_manifest(m, PREREG, h)
    forged = {**m, "condition": {"n": 5, "distribution": "normal", "replications": 5}}
    with pytest.raises(ManifestError, match="condition"):
        validate_manifest(forged, PREREG, h)


def test_executor_module_is_llm_free():
    import repro.pipeline.p2_experiments as p2
    src = Path(p2.__file__).read_text()
    for forbidden in ("anthropic", "roles.base", "roles.implementer", "LLMProvider"):
        assert forbidden not in src


# T5 -------------------------------------------------------------------------

def test_implementer_converges_within_three_rounds():
    target, tolerance = 0.80, 0.02
    state = {"value": 0.70}

    class ScriptedProvider:
        """Stands in for the model: each round proposes a config diff nudging the
        candidate in the direction named by the packet."""

        def complete(self, system, user, max_tokens=4096):
            step = 0.05 if '"direction": "under"' in user or "'direction': 'under'" in user else -0.05
            return json.dumps({"commands": [f"apply-diff {step:+.2f}"],
                               "files": {}, "notes": "nudge"})

    rounds = 0
    while abs(state["value"] - target) > tolerance and rounds < 3:
        packet = implementer.discrepancy_feedback("C1", state["value"] - target, tolerance)
        proposal = implementer.propose(ScriptedProvider(), "method spec", feedback=[packet])
        for action in implementer.to_actions(proposal):
            validate_action(action)
            if action["action"] == "run":
                state["value"] += float(action["cmd"].split()[-1])
        rounds += 1
    assert abs(state["value"] - target) <= tolerance and rounds <= 3


# T6 -------------------------------------------------------------------------

def test_verifier_package_boundary():
    import repro.roles.verifier as v
    src = Path(v.__file__).read_text()
    for forbidden in ("implementer", "p2_experiments", "p1_archaeology",
                      "daytona_client", "lifecycle"):
        assert forbidden not in src, f"verifier must not touch {forbidden}"


# T7 -------------------------------------------------------------------------

def test_dashboard_queries(stack, tmp_path):
    adapter, ledger, gates, life = stack
    att = ledger.start_attempt(RUN, "E001", "m" * 64, "snapshot", "base", "cmd", [1])
    ledger.finish_attempt(att, 0, "e" * 64)
    ledger.record_verdict(RUN, "C1", "R1", "0.51", "0.01",
                          "REPRODUCED WITHIN TOLERANCE", [att])
    from repro.dashboard import run_payload, runs_payload
    db = sqlite3.connect(ledger.path)
    db.row_factory = sqlite3.Row
    runs = runs_payload(db)
    assert runs[0]["run_id"] == RUN and runs[0]["attempts"] == 1 and runs[0]["verdicts"] == 1
    detail = run_payload(db, RUN)
    assert detail["attempts"][0]["exp_id"] == "E001"
    assert detail["verdicts"][0]["verdict"] == "REPRODUCED WITHIN TOLERANCE"


# T8 -------------------------------------------------------------------------

FIXTURE_RESULTS = [
    {"url": "https://example.invalid/repo", "title": "official repo",
     "content": "def secret(): pass", "excerpt": "code body here"},
]


def test_code_gate_three_outcomes_and_metadata_only():
    outcome, cert = evaluate_code_existence([])
    assert outcome == "NOT_FOUND"

    outcome, cert = evaluate_code_existence(FIXTURE_RESULTS,
                                            link_alive={"https://example.invalid/repo": False})
    assert outcome == "REFERENCED_BUT_DEAD"
    assert cert["dead_links"] == ["https://example.invalid/repo"]

    outcome, cert = evaluate_code_existence(FIXTURE_RESULTS,
                                            link_alive={"https://example.invalid/repo": True})
    assert outcome == "FOUND"
    dumped = json.dumps(cert)
    assert "secret" not in dumped and "code body" not in dumped  # metadata only


def test_intake_decisions():
    assert intake_decision(1, "NOT_FOUND")["proceed"]
    assert intake_decision(1, "REFERENCED_BUT_DEAD")["proceed"]
    found = intake_decision(1, "FOUND")
    assert not found["proceed"] and "certificate" in found["reason"]
    for cls in (2, 3, 4):
        d = intake_decision(cls, "NOT_FOUND")
        assert not d["proceed"] and str(cls) in d["reason"]


# T9 -------------------------------------------------------------------------

def test_demo_window_preview_lifecycle_and_gated_push(stack):
    adapter, ledger, gates, life = stack
    files = fallback_app_files([], "VERIFIED", "T")
    adapter.exec_responses["nohup"] = ExecResult(0, "200")
    result = deploy(life, adapter, ledger, RUN, files, base_snapshot="base",
                    demo_window=True)
    sid = result["sandbox_id"]
    spec = adapter.sandboxes[sid]["spec"]
    # demo window: idle-stop at 3h so a forgotten preview stops holding org
    # quota, never mid-demo; 12h TTL is still the backstop
    assert spec.auto_stop_interval == 180 and spec.ttl_minutes == 720
    assert any(k == "get_preview_link" for k, _ in adapter.calls)
    assert any(k == "create_signed_preview_url" for k, _ in adapter.calls)

    pushed = []
    with pytest.raises(PushNotApproved):
        push_output(gates, RUN, lambda: pushed.append(1))
    assert pushed == []
    gates.approve(RUN, "G3", "user")
    push_output(gates, RUN, lambda: pushed.append(1))
    assert pushed == [1]


# policy ---------------------------------------------------------------------

def test_policy_defaults_and_off_switch(tmp_path):
    policy = load_policy()
    assert policy["mc_tolerance_k"] == 3.0
    assert parallel_stages(policy) == ("intake", "archaeology")
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"parallel": {"enabled": False}}))
    assert parallel_stages(load_policy(p)) == ()

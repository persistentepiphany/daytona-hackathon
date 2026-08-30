"""Role validation layers: proposals are checked deterministically, the verifier is
sealed, discrepancy feedback carries no raw values."""

import json

import pytest

from repro.roles import builder, implementer, planner, verifier
from repro.roles.base import RoleError, extract_json


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, system, user, max_tokens=4096):
        self.calls.append((system, user))
        return self.response


GOOD_PLAN = {
    "claims": [{"id": "C1", "metric": "test_accuracy", "dataset": "d", "split": "test",
                "reported_value": 0.8, "model": "M", "params": {}, "source_loc": "Table 1"}],
    "ambiguities": [{"id": "A1", "question": "q", "config_key": "data.scale"}],
    "experiments": [{"experiment_id": "E001", "claim_id": "C1", "type": "reproduce",
                     "rule": {"id": "R1", "kind": "abs_tolerance", "target": 0.8,
                              "tolerance": 0.01, "aggregate": "mean"}}],
    "tolerances": {"C1": 0.01},
    "cost_estimate": {"sandbox_hours": 1, "notes": ""},
}


def test_planner_accepts_valid_and_rejects_off_menu():
    p = planner.propose(FakeProvider(json.dumps(GOOD_PLAN)), "paper", "objective", "quick")
    assert p["claims"][0]["id"] == "C1"

    bad = dict(GOOD_PLAN, experiments=[{"experiment_id": "E001", "claim_id": "C1",
                                        "type": "invent_new_method", "rule": {}}])
    with pytest.raises(RoleError, match="menu"):
        planner.propose(FakeProvider(json.dumps(bad)), "paper", "objective", "quick")

    bad2 = dict(GOOD_PLAN, ambiguities=[{"id": "A1", "question": "q"}])
    with pytest.raises(RoleError, match="config key"):
        planner.propose(FakeProvider(json.dumps(bad2)), "paper", "objective", "quick")


def test_discrepancy_feedback_buckets_hide_raw_values():
    fb = implementer.discrepancy_feedback("C2", delta=-0.025, tolerance=0.01)
    assert fb == {"claim_id": "C2", "direction": "under", "magnitude_bucket": "moderate"}
    assert "0.025" not in json.dumps(fb)
    assert implementer.discrepancy_feedback("C1", 0.004, 0.01)["magnitude_bucket"] == "small"
    assert implementer.discrepancy_feedback("C1", 0.2, 0.01)["magnitude_bucket"] == "large"


def test_verifier_bundle_is_sealed(tmp_path):
    exp = tmp_path / "E001"
    exp.mkdir()
    (exp / "metrics.json").write_text('{"mean_value": 0.79}')
    (exp / "stdout.log").write_text("secret implementation trace")
    (exp / "checksums.json").write_text("{}")
    bundle = verifier.sealed_evidence_bundle({"claims": []}, tmp_path)
    assert "mean_value" in bundle
    assert "secret implementation trace" not in bundle  # logs stay outside the seal


def test_verifier_cross_check_flags_disagreement():
    llm = {"verdicts": [{"experiment_id": "E001", "verdict": "NOT REPRODUCED"}]}
    det = [{"experiment_id": "E001", "verdict": "REPRODUCED WITHIN TOLERANCE"}]
    flags = verifier.cross_check(llm, det)
    assert len(flags) == 1 and "E001" in flags[0]
    llm_ok = {"verdicts": [{"experiment_id": "E001", "verdict": "REPRODUCED WITHIN TOLERANCE"}]}
    assert verifier.cross_check(llm_ok, det) == []


def test_builder_requires_endpoint_and_page():
    good = {"files": {"app.py": "x", "index.html": "y"}, "start_command": "python app.py",
            "notes": "n"}
    assert builder.propose(FakeProvider(json.dumps(good)), "brief")["start_command"]
    with pytest.raises(RoleError):
        builder.propose(FakeProvider(json.dumps({"files": {"app.py": "x"},
                                                 "start_command": "s"})), "brief")


def test_extract_json_handles_fences():
    assert extract_json('prose ```json\n{"a": 1}\n``` more') == {"a": 1}
    assert extract_json('{"b": 2}') == {"b": 2}

"""Prereg freeze, held-out annex, deterministic manifest gate, verdict rules."""

import pytest

from repro.calibration import fashion_mnist as cal
from repro.orchestrator.manifest import ManifestError, build_manifest, validate_manifest
from repro.orchestrator.prereg import build_prereg, sha256_of
from repro.pipeline import p3_verdict as p3


@pytest.fixture(scope="module")
def frozen():
    paper, claims, experiments, tolerances, seeds = cal.prereg_inputs()
    doc, annex = build_prereg(paper, claims, experiments, tolerances, seeds, rng_seed=1337)
    return doc, sha256_of(doc), annex


def test_held_out_split_is_deterministic_and_hidden(frozen):
    doc, _, annex = frozen
    assert len(annex["claims"]) == 1
    held_id = annex["claims"][0]["id"]
    assert held_id not in {c["id"] for c in doc["claims"]}
    assert held_id not in doc["tolerances"]
    assert all(e["claim_id"] != held_id for e in doc["experiments"])
    # same rng seed, same split
    paper, claims, experiments, tolerances, seeds = cal.prereg_inputs()
    doc2, annex2 = build_prereg(paper, claims, experiments, tolerances, seeds, rng_seed=1337)
    assert annex2["claims"][0]["id"] == held_id
    assert sha256_of(doc2) == sha256_of(doc)


def test_manifest_roundtrip_and_gate(frozen):
    doc, h, _ = frozen
    exp_id = doc["experiments"][0]["experiment_id"]
    m = build_manifest(doc, h, exp_id)
    assert validate_manifest(m, doc, h)

    for corrupt in (
        {"prereg_hash": "0" * 64},
        {"claim_id": "C999"},
        {"seeds": [1, 2, 3]},
        {"command": "bash runner.sh EVIL"},
        {"type": "ablation"},
        {"mutation": {"config_key": "models.C1.params.max_depth", "value": 3}},
        {"expected_outputs": []},
        {"budget": {"ttl_min": 0}},
    ):
        bad = {**m, **corrupt}
        with pytest.raises(ManifestError):
            validate_manifest(bad, doc, h)


def test_ablation_mutation_must_match_prereg(frozen):
    doc, h, _ = frozen
    entry = next(e for e in doc["experiments"] if e["type"] == "ablation")
    m = build_manifest(doc, h, entry["experiment_id"])
    assert validate_manifest(m, doc, h)
    m["mutation"] = {"config_key": entry["mutation"]["config_key"], "value": 99}
    with pytest.raises(ManifestError, match="mutation"):
        validate_manifest(m, doc, h)


def test_verdict_rules(frozen):
    doc, _, _ = frozen
    repro = next(e for e in doc["experiments"] if e["type"] == "reproduce")
    target = repro["rule"]["target"]
    tol = repro["rule"]["tolerance"]

    within = p3.judge_experiment(repro, {"mean_value": target + tol * 0.5})
    assert within["verdict"] == p3.REPRODUCED
    outside = p3.judge_experiment(repro, {"mean_value": target + tol * 2})
    assert outside["verdict"] == p3.OUTSIDE
    far = p3.judge_experiment(repro, {"mean_value": target + tol * 10})
    assert far["verdict"] == p3.NOT_REPRODUCED
    missing = p3.judge_experiment(repro, None)
    assert missing["verdict"] == p3.NOT_ATTEMPTABLE

    ablation = next(e for e in doc["experiments"] if e["type"] == "ablation")
    drop = p3.judge_experiment(ablation, {"mean_value": 0.85}, {"mean_value": 0.87})
    assert drop["verdict"] == p3.CONTROL_PASS
    no_drop = p3.judge_experiment(ablation, {"mean_value": 0.87}, {"mean_value": 0.87})
    assert no_drop["verdict"] == p3.CONTROL_FAIL

    control = next(e for e in doc["experiments"] if e["type"] == "randomized_control")
    chance = p3.judge_experiment(control, {"mean_value": 0.101})
    assert chance["verdict"] == p3.CONTROL_PASS
    signal = p3.judge_experiment(control, {"mean_value": 0.5})
    assert signal["verdict"] == p3.CONTROL_FAIL

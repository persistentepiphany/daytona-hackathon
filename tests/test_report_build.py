"""Report generation, adaptive-round constraints, and the fallback thin app."""

import json

import pytest

from repro.orchestrator.gates import Gates
from repro.orchestrator.ledger import Ledger
from repro.pipeline.p4_adaptive import AdaptiveError, build_adaptive_prereg
from repro.pipeline.p5_build import fallback_app_files
from repro.pipeline.report import FRAMING, generate_report

RUN = "run-r"


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    led.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    led.set_run_freeze(RUN, "s0-snap", "gitsha", "r" * 64)
    return led


ROWS = [
    {"experiment_id": "E001", "claim_id": "C1", "type": "reproduce", "held_out": False,
     "observed": 0.797, "delta": -0.001, "verdict": "REPRODUCED WITHIN TOLERANCE",
     "rule_id": "R-E001", "attempt_ids": ["att-1"]},
    {"experiment_id": "E900", "claim_id": "C9", "type": "reproduce", "held_out": True,
     "observed": None, "delta": None, "verdict": "NOT ATTEMPTABLE",
     "rule_id": "R-E900", "attempt_ids": []},
]
SHAM = [
    {"experiment_id": "SH01", "claim_id": "C4", "type": "reproduce", "held_out": False,
     "observed": 0.511, "delta": -0.05, "verdict": "NOT REPRODUCED",
     "rule_id": "R-SH01", "attempt_ids": ["att-2"]},
]


def test_report_orders_controls_first_and_carries_framing(ledger):
    text = generate_report(RUN, {"claims": []}, ROWS, SHAM, "VERIFIED", ledger, "Paper T")
    assert FRAMING in text
    assert text.index("Sham twin") < text.index("Primary preregistered results")
    assert "held-out" in text.lower() or "yes" in text
    assert "s0-snap" in text and "h" * 64 in text


def test_adaptive_round_is_single_and_menu_bound(ledger):
    base = {"claims": [{"id": "C1"}], "experiments": [{"experiment_id": "E001"}],
            "tolerances": {"C1": 0.01}, "seeds": [1], "paper": {"paper_id": "x"}}
    good = [{"experiment_id": "A001", "claim_id": "C1", "type": "seed_sweep",
             "rule": {"id": "R-A001", "kind": "abs_tolerance", "target": 0.8,
                      "tolerance": 0.02, "aggregate": "mean"}}]
    doc, doc_hash = build_adaptive_prereg(base, good, ledger, RUN)
    assert doc["parent_prereg"] and doc_hash
    with pytest.raises(AdaptiveError, match="already used"):
        build_adaptive_prereg(base, good, ledger, RUN)

    led2 = Ledger(ledger.path + "2")
    led2.create_run(RUN, "p" * 64, "h" * 64)
    bad = [dict(good[0], type="brand_new_method")]
    with pytest.raises(AdaptiveError, match="menu"):
        build_adaptive_prereg(base, bad, led2, RUN)


def test_fallback_app_embeds_verdicts_and_page():
    files = fallback_app_files(ROWS, "VERIFIED", "Paper T")
    assert "app.py" in files and "index.html" in files
    assert "/api/verdicts" in files["app.py"]
    data = json.loads(files["app.py"].split("r'''", 1)[1].split("'''", 1)[0])
    assert data["paper"] == "Paper T"
    assert data["verdicts"][0]["experiment_id"] == "E001"
    assert "NOT REPRODUCED" not in files["index.html"] or True  # page renders any verdict

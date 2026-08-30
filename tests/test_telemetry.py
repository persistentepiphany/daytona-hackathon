"""The bus: redaction at one site, a closed vocabulary, dual write, ordered fan-out."""

import json
import queue
import re
import threading
import time

import pytest

from repro import telemetry
from repro.orchestrator.ledger import Ledger

RUN = "run-tel"

# what a leak looks like. Deliberately fake, and deliberately not matching any real key.
FAKE_ANTHROPIC = "sk-ant-api03-NOTAREALKEY000000"
FAKE_GITHUB = "ghp_NOTAREALTOKEN0000000000"
FAKE_PAT = "github_pat_NOTAREALPAT00000000"

# a redacted assignment still reads `API_KEY=[REDACTED]`, and that is the pass case,
# so the sweep looks for an assignment whose value is anything but the marker
CREDENTIAL_SWEEP = re.compile(
    r"sk-ant-|ghp_[A-Za-z0-9]{8}|github_pat_|"
    r"(api[_-]?key|token|secret|password)\s*[=:]\s*[\"']?(?!\[REDACTED\])[^\s\"'&;,)]{6,}",
    re.IGNORECASE,
)


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    led.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    return led


# --- redaction -------------------------------------------------------------

def test_credentials_are_redacted_at_every_depth():
    dirty = {"cmd": f"export KEY={FAKE_ANTHROPIC}",
             "nested": [{"tail": f"cloning with {FAKE_GITHUB}"},
                        {"tail": f"pat {FAKE_PAT} used"}],
             "api_key": "whatever-this-is",
             "urls": ["https://example.test/cb?token=abcdefghijkl"]}
    clean = telemetry.redact(dirty)
    blob = json.dumps(clean)
    for secret in (FAKE_ANTHROPIC, FAKE_GITHUB, FAKE_PAT, "whatever-this-is",
                   "abcdefghijkl"):
        assert secret not in blob
    assert telemetry.REDACTED in clean["cmd"]
    assert clean["api_key"] == telemetry.REDACTED


def test_redaction_leaves_load_bearing_payloads_alone():
    """config_key is in every ablation manifest and mutation payload; a prefix rule
    would eat it and break manifest validation on replay."""
    payload = {"mutation": {"config_key": "models.C2.params.n_estimators", "value": 10},
               "cmd": "venv/bin/python train.py --set models.C2.params.n_estimators=10",
               "seeds": [17, 41], "exit": 0, "sha256": "a" * 64}
    assert telemetry.redact(payload) == payload


def test_redaction_is_not_behind_the_flag(ledger):
    """A run with the feed off must not write a leakier events table than one with it
    on: the flag gates fan-out and the new kinds, never scrubbing."""
    ledger.bus.enabled = False
    ledger.log_event(RUN, "archaeology_cmd", {"tail": f"key={FAKE_ANTHROPIC}"})
    row = ledger.events_for(RUN)[-1]
    assert FAKE_ANTHROPIC not in row["payload"]
    assert telemetry.REDACTED in row["payload"]


def test_no_credential_pattern_survives_anywhere_in_the_events_table(ledger):
    for payload in ({"tail": f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}"},
                    {"cmd": f"git push https://{FAKE_GITHUB}@github.test/x"},
                    {"note": f"pat={FAKE_PAT}"},
                    {"authorization": "Bearer abcdefghijklmnop"}):
        ledger.log_event(RUN, "archaeology_cmd", payload)
    ledger.bus.emit(RUN, "log.chunk", {"attempt_id": "att-1", "stream": "stdout",
                                       "text": f"echo {FAKE_ANTHROPIC}"})
    hits = [r["payload"] for r in ledger.db.execute("SELECT payload FROM events").fetchall()
            if CREDENTIAL_SWEEP.search(r["payload"])]
    assert hits == []


# --- vocabulary ------------------------------------------------------------

def test_vocabulary_is_closed(ledger):
    with pytest.raises(telemetry.TelemetryError, match="closed vocabulary"):
        ledger.bus.emit(RUN, "agent.thinking", {})
    for kind in telemetry.KINDS:
        ledger.bus.emit(RUN, kind, {"probe": True})
    assert {r["kind"] for r in ledger.events_for(RUN)} == set(telemetry.KINDS)


def test_legacy_kinds_still_write_unchanged(ledger):
    """The 26 pre-existing kinds predate the vocabulary and must keep working, with
    their payload shapes intact - kill_all and reconstruct_attempt read them back."""
    ledger.log_event(RUN, "sandbox_created", {"sandbox_id": "sbx-1", "parent_id": None})
    payload = json.loads(ledger.events_for(RUN, "sandbox_created")[0]["payload"])
    assert payload == {"sandbox_id": "sbx-1", "parent_id": None}


def test_disabled_bus_drops_new_kinds_but_keeps_legacy_rows(ledger):
    ledger.bus.enabled = False
    ledger.bus.emit(RUN, "run.done", {})
    ledger.log_event(RUN, "smoke_gate", {"exit": 0})
    kinds = [r["kind"] for r in ledger.events_for(RUN)]
    assert kinds == ["smoke_gate"]


# --- dual write and fan-out ------------------------------------------------

def test_emit_writes_the_table_and_the_queue(ledger):
    q = ledger.bus.subscribe(RUN)
    _, row_id = ledger.bus.emit(RUN, "run.done", {"ok": True})
    frame = q.get(timeout=1)
    assert frame["id"] == row_id and frame["kind"] == "run.done"
    assert ledger.events_for(RUN)[0]["kind"] == "run.done"


def test_fanout_is_prompt_and_ordered(ledger):
    q = ledger.bus.subscribe(RUN)
    t0 = time.monotonic()
    for i in range(50):
        ledger.bus.emit(RUN, "budget.tick", {"kind": "sandbox_minutes", "spent": i,
                                             "ceiling": 100})
    got = [q.get(timeout=1)["payload"]["spent"] for _ in range(50)]
    assert got == list(range(50))
    assert time.monotonic() - t0 < 1.0


def test_slow_subscriber_never_blocks_the_run(ledger):
    """A backgrounded browser tab must not be able to stall a training run: the queue
    is bounded and drops its oldest frame rather than blocking emit()."""
    q = ledger.bus.subscribe(RUN, maxsize=4)
    for i in range(40):
        ledger.bus.emit(RUN, "budget.tick", {"kind": "x", "spent": i, "ceiling": 100})
    drained = []
    while True:
        try:
            drained.append(q.get_nowait()["payload"]["spent"])
        except queue.Empty:
            break
    assert len(drained) <= 4 and drained[-1] == 39      # newest survived
    assert len(ledger.events_for(RUN)) == 40            # the table lost nothing


def test_unsubscribe_stops_delivery(ledger):
    q = ledger.bus.subscribe(RUN)
    ledger.bus.unsubscribe(RUN, q)
    ledger.bus.emit(RUN, "run.done", {})
    assert ledger.bus.subscriber_count(RUN) == 0
    with pytest.raises(queue.Empty):
        q.get_nowait()


def test_row_ids_are_monotonic_under_threads(ledger):
    def worker(n):
        for i in range(25):
            ledger.bus.emit(RUN, "log.chunk", {"attempt_id": f"att-{n}",
                                               "stream": "stdout", "text": str(i)})
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [r["id"] for r in ledger.events_after(RUN, 0)]
    assert ids == sorted(ids) == list(range(1, 101))
    assert [r["id"] for r in ledger.events_after(RUN, 60)] == list(range(61, 101))

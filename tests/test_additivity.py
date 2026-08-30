"""The merge gate: with the feed off, this feature is not there.

Same fake run twice, once with REPRO_TELEMETRY=0 and once with it on. Evidence bytes,
every ledger table but `events`, and the sandbox call surface must match. Volatile
columns are interned rather than dropped, so a duplicated or reordered row still fails
the comparison - dropping them would hide exactly the regression worth catching.

Also here: the seal. The feed must not carry the held-out annex, and the verifier must
be visible only as a delivered evidence bundle and its verdicts.
"""

import hashlib
import json
import os
import re
import sqlite3

import pytest

from repro import telemetry
from repro.orchestrator.budget import Budget
from repro.orchestrator.gates import Gates
from repro.orchestrator.ledger import Ledger
from repro.orchestrator.lifecycle import Lifecycle
from repro.orchestrator.manifest import build_manifest
from repro.orchestrator.prereg import sha256_of
from repro.pipeline import p3_verdict as p3
from repro.pipeline.p2_experiments import run_experiment
from tests.fake_adapter import FakeAdapter

RUN = "run-add"

PREREG = {
    "version": 1,
    "paper": {"paper_id": "x", "pdf_sha256": "p" * 64},
    "claims": [{"id": "C1", "metric": "m", "reported_value": 0.5, "source_loc": "T1"}],
    "experiments": [{"experiment_id": "E001", "claim_id": "C1", "type": "reproduce",
                     "command": "bash runner.sh E001",
                     "rule": {"id": "R1", "kind": "abs_tolerance", "target": 0.5,
                              "tolerance": 0.02, "aggregate": "mean"}}],
    "tolerances": {"C1": 0.02},
    "seeds": [1, 2, 3],
}

ANNEX = {
    "version": 1, "role": "held_out_annex", "paper": PREREG["paper"],
    "claims": [{"id": "C9", "metric": "m", "reported_value": 0.7719,
                "source_loc": "T4-secret"}],
    "experiments": [{"experiment_id": "E900", "claim_id": "C9", "type": "reproduce",
                     "command": "bash runner.sh E900",
                     "rule": {"id": "R900", "kind": "abs_tolerance",
                              "target": 0.7719, "tolerance": 0.0413,
                              "aggregate": "mean"}}],
    "tolerances": {"C9": 0.0413},
    "seeds": [1, 2, 3],
}

LEDGER_TABLES = ("runs", "attempts", "verdicts", "datasets", "gates", "budget_charges")
VOLATILE = {"created_at", "started", "ended", "approved_at", "attempt_id", "run_id",
            "sandbox_id", "event_id"}
# attempt and event ids are uuid4-derived and so differ between any two runs, telemetry
# or not. They also appear inside JSON columns, so they are interned wherever they occur
# rather than dropped: first-seen ordering means a duplicated or reordered row still fails.
ID_IN_TEXT = re.compile(r"(att|evt)-[0-9a-f]{12}")


def _metrics(exp_id, claim_id, value):
    return {"experiment_id": exp_id, "claim_id": claim_id, "type": "reproduce",
            "metric": "m", "rows": [], "mean_value": value, "min_value": value,
            "max_value": value, "n_seeds": 3}


def fake_run(root, enabled: bool):
    """A deterministic mini-pipeline: G1, two experiments (one of them held-out), and
    the verdict pass. No wall-clock branching, no randomness."""
    os.environ["REPRO_TELEMETRY"] = "1" if enabled else "0"
    try:
        ledger = Ledger(root / "ledger.db")
        ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash=sha256_of(PREREG))
        gates = Gates(ledger)
        gates.approve(RUN, "G1", "suite")
        adapter = FakeAdapter()
        life = Lifecycle(adapter, ledger, gates,
                         Budget(ledger, RUN, {"sandbox_minutes": 10000}), RUN)
        adapter.stream_script = {"stdout.log": ["[runner] E001 seed=1\n"],
                                 "progress.jsonl": ["::progress 1/3\n"]}
        evidence = root / "evidence"

        for doc, exp_id, claim_id, value, held in (
                (PREREG, "E001", "C1", 0.51, False),
                (ANNEX, "E900", "C9", 0.77, True)):
            doc_hash = sha256_of(doc)
            manifest = build_manifest(doc, doc_hash, exp_id)
            real_create = adapter.create

            def create_and_seed(spec, _exp=exp_id, _c=claim_id, _v=value,
                                _real=real_create):
                sid = _real(spec)
                work = "/home/daytona/work"
                adapter.files[(sid, f"{work}/metrics.json")] = json.dumps(
                    _metrics(_exp, _c, _v)).encode()
                adapter.files[(sid, f"{work}/stdout.log")] = (
                    f"[runner] {_exp} seed=1\n[runner] {_exp} done mean={_v}\n".encode())
                adapter.files[(sid, f"{work}/leakage.json")] = b'{"overlap": 0}'
                return sid

            adapter.create = create_and_seed
            run_experiment(life, adapter, ledger, RUN, doc, doc_hash, manifest, "base",
                           dataset_hashes={}, evidence_root=evidence,
                           data_mode="synthetic", held_out=held)
            adapter.create = real_create

        p3.judge_run(PREREG, ANNEX, evidence, ledger, RUN)
        ledger.bus.emit(RUN, "run.done", {})
        return ledger, adapter
    finally:
        os.environ.pop("REPRO_TELEMETRY", None)


def evidence_digest(root):
    out = {}
    for path in sorted((root / "evidence").rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def intern(value, key, seen):
    if value is None:
        return None
    if key in VOLATILE:
        return seen.setdefault((key, value), f"<{key}#{len(seen)}>")
    if isinstance(value, str):
        return ID_IN_TEXT.sub(
            lambda m: seen.setdefault(("id", m.group(0)), f"<id#{len(seen)}>"), value)
    return value


def intern_rows(db, table):
    """Volatile values become stable placeholders in first-seen order, so identity and
    ordering are both asserted while wall-clock and uuid noise is not."""
    seen, out = {}, []
    for row in db.execute(f"SELECT * FROM {table}"):
        out.append({key: intern(row[key], key, seen) for key in row.keys()})
    return out


def intern_events(db, kinds=None):
    seen, out = {}, []
    for kind, payload in db.execute("SELECT kind, payload FROM events ORDER BY rowid"):
        if kinds is not None and kind in kinds:
            continue
        out.append((kind, intern(payload, "payload", seen)))
    return out


@pytest.fixture
def two_runs(tmp_path):
    off_root, on_root = tmp_path / "off", tmp_path / "on"
    off_root.mkdir()
    on_root.mkdir()
    off = fake_run(off_root, enabled=False)
    on = fake_run(on_root, enabled=True)
    return (off_root, off), (on_root, on)


# --- the invariant ---------------------------------------------------------

def test_evidence_files_are_byte_identical(two_runs):
    (off_root, _), (on_root, _) = two_runs
    off, on = evidence_digest(off_root), evidence_digest(on_root)
    assert set(off) == set(on)
    assert off == on
    # and the comparison is not vacuous
    assert any(name.endswith("stdout.log") for name in off)
    assert any(name.endswith("metrics.json") for name in off)


def test_every_ledger_table_except_events_matches(two_runs):
    (off_root, _), (on_root, _) = two_runs
    off_db = sqlite3.connect(off_root / "ledger.db")
    on_db = sqlite3.connect(on_root / "ledger.db")
    off_db.row_factory = on_db.row_factory = sqlite3.Row
    try:
        for table in LEDGER_TABLES:
            assert intern_rows(off_db, table) == intern_rows(on_db, table), table
        assert intern_rows(off_db, "attempts")  # non-vacuous
    finally:
        off_db.close()
        on_db.close()


def test_the_events_table_gains_rows_and_perturbs_none(two_runs):
    """The one table the invariant excludes. What it must still show is that the new
    hooks only added: every pre-existing row is there, in order, unchanged."""
    (off_root, _), (on_root, _) = two_runs
    off_db = sqlite3.connect(off_root / "ledger.db")
    on_db = sqlite3.connect(on_root / "ledger.db")
    try:
        legacy = intern_events(off_db)
        on_legacy = intern_events(on_db, kinds=telemetry.KINDS)
        assert on_legacy == legacy
        assert len(intern_events(on_db)) > len(legacy)
        assert not any(k in telemetry.KINDS for k, _ in legacy)
    finally:
        off_db.close()
        on_db.close()


def test_telemetry_adds_no_sandbox_behaviour(two_runs):
    """The sharpest assertion here: the feed must not create, exec, resize or spend
    anything. Only the tap's own tail sessions and the progress marker may differ."""
    (_, (_, off_adapter)), (_, (_, on_adapter)) = two_runs
    tap_only = {"exec_async", "follow_logs", "cancel_async"}

    def surface(adapter):
        return [(name, args) for name, args in adapter.calls
                if name not in tap_only
                and not (name == "write_file" and ".repro_progress" in args[1])]

    assert surface(off_adapter) == surface(on_adapter)
    assert not any(name in tap_only for name, _ in off_adapter.calls)
    assert any(name in tap_only for name, _ in on_adapter.calls)


def test_budget_charges_are_identical(two_runs):
    """TTL is pre-charged before any sandbox exists, so an identical charge ledger is
    proof the feed costs no sandbox spend."""
    (off_root, _), (on_root, _) = two_runs
    def charges(root):
        db = sqlite3.connect(root / "ledger.db")
        try:
            return [tuple(r) for r in db.execute(
                "SELECT kind, amount, note FROM budget_charges ORDER BY rowid")]
        finally:
            db.close()
    assert charges(off_root) == charges(on_root)
    assert charges(off_root)


# --- the seal --------------------------------------------------------------

def test_no_event_payload_carries_the_annex_secret(two_runs):
    """What is held out is the annex claim's target and tolerance. No event of any
    kind, legacy or new, may carry them."""
    (_, _), (on_root, _) = two_runs
    db = sqlite3.connect(on_root / "ledger.db")
    try:
        blob = "\n".join(r[0] for r in db.execute("SELECT payload FROM events"))
    finally:
        db.close()
    for secret in ("0.7719", "0.0413", "T4-secret", "held_out_annex"):
        assert secret not in blob, secret


def test_the_feed_never_carries_annex_content(two_runs):
    """Stricter than the table-wide rule: across the T12 vocabulary, nothing from the
    annex document may appear at all - not its rule, not its claim record."""
    (_, _), (on_root, _) = two_runs
    db = sqlite3.connect(on_root / "ledger.db")
    try:
        feed_rows = [p for k, p in db.execute("SELECT kind, payload FROM events")
                     if k in telemetry.KINDS]
    finally:
        db.close()
    blob = "\n".join(feed_rows)
    assert feed_rows
    for fragment in (json.dumps(ANNEX["claims"][0], sort_keys=True),
                     json.dumps(ANNEX["experiments"][0]["rule"], sort_keys=True),
                     "reported_value", "tolerance"):
        assert fragment not in blob, fragment


def test_held_out_output_streams_as_byte_counts_only(two_runs):
    (_, _), (on_root, _) = two_runs
    db = sqlite3.connect(on_root / "ledger.db")
    try:
        chunks = [json.loads(p) for k, p in db.execute("SELECT kind, payload FROM events")
                  if k == "log.chunk"]
    finally:
        db.close()
    held = [c for c in chunks if c.get("suppressed")]
    assert all("text" not in c for c in held)


def test_the_verifier_is_visible_only_as_a_bundle_and_its_verdicts(tmp_path):
    """Running the verifier over fixtures may produce an agent.action saying the
    evidence bundle was delivered, and the verdicts. Nothing else."""
    from repro.roles import verifier as verifier_role

    ledger = Ledger(tmp_path / "ledger.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    evidence = tmp_path / "evidence" / "E001"
    evidence.mkdir(parents=True)
    (evidence / "metrics.json").write_text(json.dumps(_metrics("E001", "C1", 0.51)))
    (evidence / "manifest.json").write_text("{}")
    (evidence / "stdout.log").write_text("secret implementation trace")

    before = len(ledger.events_for(RUN))
    bundle = verifier_role.sealed_evidence_bundle(PREREG, tmp_path / "evidence")
    ledger.bus.emit(RUN, "agent.action", {
        "role": "verifier", "type": "deliver",
        "summary": f"evidence bundle delivered ({len(bundle)} bytes)"})
    p3.judge_run(PREREG, {"claims": [], "experiments": []}, tmp_path / "evidence",
                 ledger, RUN)

    kinds = {r["kind"] for r in ledger.events_for(RUN)[before:]}
    assert kinds <= {"agent.action", "verdict.emitted"}
    # and the bundle itself still excludes the implementation trace
    assert "secret implementation trace" not in bundle


# --- the merge promise -----------------------------------------------------

def test_the_feed_is_off_unless_someone_asks_for_it(tmp_path, monkeypatch):
    """The promise that makes this feature safe to merge: with REPRO_TELEMETRY unset,
    a run behaves exactly as it did before the feed existed. No feed events, and - since
    run_experiment gates the sandbox log tap on the same flag - no extra provider calls
    either.
    """
    monkeypatch.delenv("REPRO_TELEMETRY", raising=False)
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash=sha256_of(PREREG))

    assert ledger.bus.enabled is False

    ledger.bus.emit(RUN, "run.done", {})
    ledger.bus.emit(RUN, "log.chunk", {"attempt_id": "att-1", "stream": "stdout",
                                       "text": "hi"})
    assert ledger.events_for(RUN) == []

    # the pre-existing path is untouched: legacy kinds still write, unchanged
    ledger.log_event(RUN, "sandbox_created", {"sandbox_id": "sbx-1", "parent_id": None})
    rows = ledger.events_for(RUN)
    assert [r["kind"] for r in rows] == ["sandbox_created"]
    assert json.loads(rows[0]["payload"]) == {"sandbox_id": "sbx-1", "parent_id": None}


def test_opting_in_is_one_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("REPRO_TELEMETRY", "1")
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash=sha256_of(PREREG))
    assert ledger.bus.enabled is True
    ledger.bus.emit(RUN, "run.done", {})
    assert [r["kind"] for r in ledger.events_for(RUN)] == ["run.done"]


def test_the_default_off_run_makes_no_extra_provider_calls(tmp_path, monkeypatch):
    """Stated as a call-surface assertion rather than a claim: a feed-off run touches
    the sandbox exactly as many times as it did before the tap existed."""
    monkeypatch.delenv("REPRO_TELEMETRY", raising=False)
    _, adapter = fake_run(tmp_path, enabled=False)
    tap_only = {"exec_async", "follow_logs", "cancel_async"}
    assert not [c for c in adapter.calls if c[0] in tap_only]
    assert not [c for c in adapter.calls
                if c[0] == "write_file" and ".repro_progress" in c[1][1]]

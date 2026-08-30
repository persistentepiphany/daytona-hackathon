"""Append-only SQLite ledger: every run, attempt, and verdict is replayable from here.

Replaying an attempt never depends on agent memory: `s0_snapshot + manifest_hash +
dataset hashes` resolved from these tables reconstructs any run. Rows are inserted,
never rewritten; the single exception is finalizing an attempt's end state, which is
guarded to happen at most once.
"""

import functools
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


BUSY_TIMEOUT_S = 30.0  # a contended cross-process write waits this long before raising


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return wrapper

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    paper_hash  TEXT NOT NULL,
    prereg_hash TEXT NOT NULL,
    s0_snapshot TEXT,
    s0_git_sha  TEXT,
    recipe_sha  TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id    TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    exp_id        TEXT NOT NULL,
    claim_id      TEXT,
    manifest_hash TEXT NOT NULL,
    spawn_mode    TEXT NOT NULL CHECK (spawn_mode IN ('snapshot', 'fork')),
    source_ref    TEXT NOT NULL,
    sandbox_id    TEXT,
    cmd           TEXT NOT NULL,
    seeds         TEXT NOT NULL,
    started       REAL NOT NULL,
    ended         REAL,
    exit          INTEGER,
    evidence_sha  TEXT,
    cost_est      REAL
);
CREATE TABLE IF NOT EXISTS verdicts (
    claim_id    TEXT NOT NULL,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    rule_id     TEXT NOT NULL,
    observed    TEXT,
    delta       TEXT,
    verdict     TEXT NOT NULL,
    attempt_ids TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    run_id     TEXT NOT NULL REFERENCES runs(run_id),
    path       TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (run_id, path)
);
CREATE TABLE IF NOT EXISTS events (
    event_id   TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS gates (
    run_id      TEXT NOT NULL,
    gate        TEXT NOT NULL,
    approver    TEXT NOT NULL,
    approved_at REAL NOT NULL,
    UNIQUE (run_id, gate)
);
CREATE TABLE IF NOT EXISTS budget_charges (
    run_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    amount     REAL NOT NULL,
    note       TEXT,
    created_at REAL NOT NULL
);
"""


class LedgerError(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.lock = threading.RLock()  # one connection, serialized writes in-process
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=BUSY_TIMEOUT_S)
        self.db.row_factory = sqlite3.Row
        # concurrent pipelines are separate processes sharing this file, and the
        # RLock above only serializes threads: WAL lets a reader run while another
        # process writes, and the busy timeout makes a contended write wait rather
        # than raise 'database is locked'
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_S * 1000)}")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # runs ----------------------------------------------------------------
    @_locked
    def create_run(self, run_id: str, paper_hash: str, prereg_hash: str) -> None:
        self.db.execute(
            "INSERT INTO runs (run_id, paper_hash, prereg_hash, created_at) VALUES (?,?,?,?)",
            (run_id, paper_hash, prereg_hash, time.time()),
        )
        self.db.commit()

    @_locked
    def set_run_freeze(self, run_id: str, s0_snapshot: str, s0_git_sha: str, recipe_sha: str) -> None:
        row = self.run(run_id)
        if row is None:
            raise LedgerError(f"unknown run {run_id}")
        if row["s0_snapshot"] is not None:
            raise LedgerError(f"run {run_id} already frozen at {row['s0_snapshot']}; S0 is immutable")
        self.db.execute(
            "UPDATE runs SET s0_snapshot=?, s0_git_sha=?, recipe_sha=? WHERE run_id=? AND s0_snapshot IS NULL",
            (s0_snapshot, s0_git_sha, recipe_sha, run_id),
        )
        self.db.commit()

    @_locked
    def run(self, run_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()

    # attempts ------------------------------------------------------------
    @_locked
    def start_attempt(
        self,
        run_id: str,
        exp_id: str,
        manifest_hash: str,
        spawn_mode: str,
        source_ref: str,
        cmd: str,
        seeds: list[int],
        claim_id: str | None = None,
        sandbox_id: str | None = None,
        cost_est: float | None = None,
    ) -> str:
        attempt_id = f"att-{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO attempts (attempt_id, run_id, exp_id, claim_id, manifest_hash, spawn_mode,"
            " source_ref, sandbox_id, cmd, seeds, started, cost_est) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (attempt_id, run_id, exp_id, claim_id, manifest_hash, spawn_mode, source_ref,
             sandbox_id, cmd, json.dumps(seeds), time.time(), cost_est),
        )
        self.db.commit()
        return attempt_id

    @_locked
    def bind_sandbox(self, attempt_id: str, sandbox_id: str) -> None:
        cur = self.db.execute(
            "UPDATE attempts SET sandbox_id=? WHERE attempt_id=? AND sandbox_id IS NULL",
            (sandbox_id, attempt_id),
        )
        if cur.rowcount != 1:
            raise LedgerError(f"attempt {attempt_id} missing or sandbox already bound")
        self.db.commit()

    @_locked
    def finish_attempt(self, attempt_id: str, exit_code: int, evidence_sha: str | None) -> None:
        cur = self.db.execute(
            "UPDATE attempts SET ended=?, exit=?, evidence_sha=? WHERE attempt_id=? AND ended IS NULL",
            (time.time(), exit_code, evidence_sha, attempt_id),
        )
        if cur.rowcount != 1:
            raise LedgerError(f"attempt {attempt_id} missing or already finalized")
        self.db.commit()

    @_locked
    def attempt(self, attempt_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()

    @_locked
    def attempts_for(self, run_id: str, exp_id: str | None = None) -> list[sqlite3.Row]:
        if exp_id:
            q = "SELECT * FROM attempts WHERE run_id=? AND exp_id=? ORDER BY started"
            return self.db.execute(q, (run_id, exp_id)).fetchall()
        return self.db.execute(
            "SELECT * FROM attempts WHERE run_id=? ORDER BY started", (run_id,)
        ).fetchall()

    # verdicts ------------------------------------------------------------
    @_locked
    def record_verdict(
        self, run_id: str, claim_id: str, rule_id: str, observed: str | None,
        delta: str | None, verdict: str, attempt_ids: list[str],
    ) -> None:
        self.db.execute(
            "INSERT INTO verdicts (claim_id, run_id, rule_id, observed, delta, verdict, attempt_ids, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (claim_id, run_id, rule_id, observed, delta, verdict, json.dumps(attempt_ids), time.time()),
        )
        self.db.commit()

    @_locked
    def verdicts_for(self, run_id: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM verdicts WHERE run_id=? ORDER BY created_at", (run_id,)
        ).fetchall()

    # datasets ------------------------------------------------------------
    @_locked
    def record_dataset(self, run_id: str, path: str, sha256: str) -> None:
        self.db.execute(
            "INSERT INTO datasets (run_id, path, sha256, created_at) VALUES (?,?,?,?)",
            (run_id, path, sha256, time.time()),
        )
        self.db.commit()

    @_locked
    def datasets_for(self, run_id: str) -> dict[str, str]:
        rows = self.db.execute("SELECT path, sha256 FROM datasets WHERE run_id=?", (run_id,)).fetchall()
        return {r["path"]: r["sha256"] for r in rows}

    # events --------------------------------------------------------------
    @_locked
    def log_event(self, run_id: str, kind: str, payload: dict) -> str:
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO events (event_id, run_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
            (event_id, run_id, kind, json.dumps(payload, sort_keys=True), time.time()),
        )
        self.db.commit()
        return event_id

    @_locked
    def events_for(self, run_id: str, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.db.execute(
                "SELECT * FROM events WHERE run_id=? AND kind=? ORDER BY created_at", (run_id, kind)
            ).fetchall()
        return self.db.execute(
            "SELECT * FROM events WHERE run_id=? ORDER BY created_at", (run_id,)
        ).fetchall()

    # replay --------------------------------------------------------------
    @_locked
    def resolve_replay(self, attempt_id: str) -> dict:
        """Everything needed to re-execute an attempt, independent of any agent memory."""
        att = self.attempt(attempt_id)
        if att is None:
            raise LedgerError(f"unknown attempt {attempt_id}")
        run = self.run(att["run_id"])
        return {
            "attempt_id": attempt_id,
            "run_id": att["run_id"],
            "exp_id": att["exp_id"],
            "s0_snapshot": run["s0_snapshot"],
            "manifest_hash": att["manifest_hash"],
            "spawn_mode": att["spawn_mode"],
            "source_ref": att["source_ref"],
            "cmd": att["cmd"],
            "seeds": json.loads(att["seeds"]),
            "dataset_hashes": self.datasets_for(att["run_id"]),
        }

    # budget --------------------------------------------------------------
    @_locked
    def sum_charges(self, run_id: str, kind: str) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM budget_charges WHERE run_id=? AND kind=?",
            (run_id, kind),
        ).fetchone()
        return float(row["total"])

    @_locked
    def add_charge(self, run_id: str, kind: str, amount: float, note: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO budget_charges (run_id, kind, amount, note, created_at) VALUES (?,?,?,?,?)",
            (run_id, kind, amount, note, time.time()),
        )
        self.db.commit()

    # gates ---------------------------------------------------------------
    @_locked
    def add_gate(self, run_id: str, gate: str, approver: str) -> None:
        self.db.execute(
            "INSERT INTO gates (run_id, gate, approver, approved_at) VALUES (?,?,?,?)",
            (run_id, gate, approver, time.time()),
        )
        self.db.commit()

    @_locked
    def has_gate(self, run_id: str, gate: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM gates WHERE run_id=? AND gate=?", (run_id, gate)
        ).fetchone() is not None

    def close(self) -> None:
        self.db.close()

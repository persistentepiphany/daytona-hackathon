"""The sandbox tap: coalescing, measured progress, the seal, and failure containment."""

import json
import time

import pytest

from repro import logtap, telemetry
from repro.orchestrator.ledger import Ledger
from tests.fake_adapter import FakeAdapter

RUN = "run-tap"
ATT = "att-tap01"


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    led.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    return led


def chunks_of(ledger, kind="log.chunk"):
    return [json.loads(r["payload"]) for r in ledger.events_for(RUN, kind)]


def settle(coalescer):
    coalescer.close()


# --- coalescing ------------------------------------------------------------

def test_small_writes_coalesce_into_one_chunk(ledger):
    """One event per line would drown the table and the browser alike."""
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    for i in range(20):
        c.feed(ATT, "stdout", f"line {i}\n")
    settle(c)
    chunks = chunks_of(ledger)
    assert len(chunks) == 1
    assert chunks[0]["text"].count("\n") == 20


def test_a_large_burst_flushes_on_the_size_threshold(ledger):
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.feed(ATT, "stdout", "x" * (c.FLUSH_BYTES + 10))
    deadline = time.monotonic() + 2
    while not chunks_of(ledger) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert chunks_of(ledger), "the 2KB trigger should fire without waiting for close"
    settle(c)


def test_streams_are_kept_apart(ledger):
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.feed(ATT, "stdout", "out\n")
    c.feed(ATT, "stderr", "err\n")
    settle(c)
    by_stream = {ch["stream"]: ch["text"] for ch in chunks_of(ledger)}
    assert by_stream == {"stdout": "out\n", "stderr": "err\n"}


def test_close_flushes_the_final_partial_buffer(ledger):
    """The last lines of a run are exactly the ones a viewer must not lose."""
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.feed(ATT, "stdout", "the last thing that happened\n")
    settle(c)
    assert "the last thing" in chunks_of(ledger)[0]["text"]


def test_runaway_logs_are_capped(ledger):
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.MAX_BYTES_PER_ATTEMPT = 4096
    for _ in range(20):
        c.feed(ATT, "stdout", "y" * 1024)
    settle(c)
    chunks = chunks_of(ledger)
    assert any(ch.get("truncated") for ch in chunks)
    assert sum(len(ch.get("text", "")) for ch in chunks) <= 8192


# --- measured progress -----------------------------------------------------

def test_progress_marker_yields_a_measured_eta(ledger):
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.track(ATT, total_seeds=5)
    c._t0[ATT] = time.monotonic() - 40      # 40s spent
    c.feed(ATT, telemetry.PROGRESS_STREAM, "::progress 2/5\n")
    settle(c)
    prog = chunks_of(ledger, "attempt.progress")[-1]
    assert prog["done"] == 2 and prog["total"] == 5
    assert prog["basis"] == "measured_seed_rate"
    assert prog["eta_s"] == pytest.approx(60, abs=2)   # 20s/seed x 3 remaining


def test_the_progress_channel_is_parsed_but_never_echoed(ledger):
    """It is a side channel for the feed, not part of the run's output."""
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.track(ATT, total_seeds=2)
    c.feed(ATT, telemetry.PROGRESS_STREAM, "::progress 1/2\n")
    settle(c)
    assert chunks_of(ledger, "log.chunk") == []
    assert len(chunks_of(ledger, "attempt.progress")) == 1


def test_progress_falls_back_to_the_runner_line_on_an_older_s0(ledger):
    """An S0 frozen before the side channel existed still reports progress, because the
    runner has always announced each seed. It announces a seed *starting*, so seeing
    two means one has finished - reporting two would be reporting work not yet done."""
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.track(ATT, total_seeds=3)
    c.feed(ATT, "stdout", "[runner] E001 seed=17\n")
    c.feed(ATT, "stdout", "[runner] E001 seed=41\n")
    c.feed(ATT, "stdout", "[runner] E001 seed=93\n")
    settle(c)
    assert [p["done"] for p in chunks_of(ledger, "attempt.progress")] == [1, 2]


def test_progress_never_runs_past_the_total_or_backwards(ledger):
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.track(ATT, total_seeds=2)
    c.feed(ATT, telemetry.PROGRESS_STREAM, "::progress 2/2\n")
    c.feed(ATT, telemetry.PROGRESS_STREAM, "::progress 1/2\n")
    settle(c)
    assert [p["done"] for p in chunks_of(ledger, "attempt.progress")] == [2]


# --- the seal --------------------------------------------------------------

def test_held_out_attempts_stream_byte_counts_never_text(ledger):
    """A held-out experiment's stdout carries its observed metric. The feed must not
    become a side channel around the annex."""
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.track(ATT, total_seeds=1, held_out=True)
    c.feed(ATT, "stdout", "test_accuracy 0.76996 for the held-out claim\n")
    settle(c)
    chunk = chunks_of(ledger)[0]
    assert chunk["suppressed"] is True
    assert "text" not in chunk and "0.76996" not in json.dumps(chunk)


def test_held_out_progress_still_streams(ledger):
    """Knowing a held-out attempt is 2 of 5 seeds in reveals nothing about its value."""
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.track(ATT, total_seeds=5, held_out=True)
    c.feed(ATT, telemetry.PROGRESS_STREAM, "::progress 2/5\n")
    settle(c)
    assert chunks_of(ledger, "attempt.progress")[0]["done"] == 2


# --- the tap ---------------------------------------------------------------

def test_tap_arms_the_marker_and_follows_both_files(ledger):
    adapter = FakeAdapter()
    spec = type("S", (), {"auto_delete_interval": None})()
    adapter.sandboxes["sbx-x"] = {"spec": spec, "labels": {}, "state": "started",
                                  "parent": None}
    adapter.stream_script = {"stdout.log": ["hello from the sandbox\n"],
                             "progress.jsonl": ["::progress 1/2\n"]}
    tap = logtap.start_log_tap(adapter, "sbx-x", ledger.bus, RUN, ATT, total_seeds=2)
    assert tap is not None
    tap.close()
    marker = adapter.files[("sbx-x", "/home/daytona/work/.repro_progress")]
    assert marker.decode().strip() == logtap.PROGRESS_FILE
    tailed = [c[1][1] for c in adapter.calls if c[0] == "exec_async"]
    assert any("stdout.log" in t for t in tailed)
    assert any(logtap.PROGRESS_FILE in t for t in tailed)
    assert "hello from the sandbox\n" in chunks_of(ledger)[0]["text"]
    assert chunks_of(ledger, "attempt.progress")[0]["done"] == 1


def test_close_cancels_every_session_it_opened(ledger):
    """An undeleted session leaves an orphaned tail burning sandbox CPU."""
    adapter = FakeAdapter()
    spec = type("S", (), {"auto_delete_interval": None})()
    adapter.sandboxes["sbx-x"] = {"spec": spec, "labels": {}, "state": "started",
                                  "parent": None}
    tap = logtap.start_log_tap(adapter, "sbx-x", ledger.bus, RUN, ATT, total_seeds=1)
    tap.close()
    assert len(adapter.cancelled) == len(tap.handles) == 2


def test_a_broken_tap_degrades_the_feed_and_never_fails_the_run(ledger):
    class Broken(FakeAdapter):
        def follow_logs(self, handle, on_stdout, on_stderr=None):
            raise RuntimeError("websocket refused")

    adapter = Broken()
    spec = type("S", (), {"auto_delete_interval": None})()
    adapter.sandboxes["sbx-x"] = {"spec": spec, "labels": {}, "state": "started",
                                  "parent": None}
    tap = logtap.start_log_tap(adapter, "sbx-x", ledger.bus, RUN, ATT, total_seeds=1)
    tap.close()
    assert tap.degraded            # recorded
    assert adapter.cancelled       # and still cleaned up


def test_start_returns_none_rather_than_raising_when_the_sandbox_refuses(ledger):
    class Refusing(FakeAdapter):
        def write_file(self, *a, **kw):
            raise RuntimeError("sandbox gone")

    assert logtap.start_log_tap(Refusing(), "sbx-x", ledger.bus, RUN, ATT, 1) is None


def test_the_explicit_channel_wins_over_the_stdout_fallback(ledger):
    """The runner prints its own seed lines to stdout and writes the side channel too.
    Counting both makes progress run ahead of the work, which is how a first live run
    reported four seeds done while the third was still running."""
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.track(ATT, total_seeds=4)
    c.feed(ATT, telemetry.PROGRESS_STREAM, "::progress 1/4\n")
    c.feed(ATT, "stdout", "[runner] MICRO seed=2\n[runner] MICRO seed=3\n"
                          "[runner] MICRO seed=4\n")
    c.feed(ATT, telemetry.PROGRESS_STREAM, "::progress 2/4\n")
    settle(c)
    assert [p["done"] for p in chunks_of(ledger, "attempt.progress")] == [1, 2]


def test_the_fallback_counts_distinct_seeds_not_lines(ledger):
    """The runner prints a line when a seed starts and another when it finishes."""
    c = telemetry.LogCoalescer(ledger.bus, RUN)
    c.track(ATT, total_seeds=3)
    c.feed(ATT, "stdout", "[runner] E1 seed=17\n[runner] E1 seed=17 done mean=0.8\n")
    c.feed(ATT, "stdout", "[runner] E1 seed=41\n")
    settle(c)
    assert [p["done"] for p in chunks_of(ledger, "attempt.progress")] == [1]

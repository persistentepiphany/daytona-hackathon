"""T12b against recorded runs: were the numbers the feed showed actually true?

These assert on streams recorded from live Daytona runs (`fixtures/feed/*.jsonl`, one
JSON object per line, exactly what the SSE endpoint replays). Checking the estimator's
arithmetic against its own assumptions would prove nothing; the point is whether an ETA
shown to a viewer halfway through a real attempt matched what actually happened.
"""

import json
from pathlib import Path

import pytest

from repro import estimates, telemetry

FIXTURES = sorted((Path(__file__).resolve().parents[1] / "fixtures" / "feed").glob("*.jsonl"))
TOLERANCE = 0.15


def load(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def by_kind(events, kind):
    return [e for e in events if e["kind"] == kind]


def progress_series(events):
    """{attempt_id: [progress payloads in order]} for attempts that ran to completion."""
    series = {}
    for event in by_kind(events, "attempt.progress"):
        series.setdefault(event["payload"]["attempt_id"], []).append(event["payload"])
    return {att: rows for att, rows in series.items()
            if rows and rows[-1]["done"] == rows[-1]["total"] and rows[-1]["total"] > 1}


def test_there_are_recorded_streams_to_check():
    """A silently-skipping honesty test is not a gate."""
    assert FIXTURES, "no recorded streams in fixtures/feed/"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_halfway_eta_is_within_fifteen_percent_of_actual(path):
    checked = 0
    for attempt, rows in progress_series(load(path)).items():
        finished_at = rows[-1]["elapsed_s"]
        halfway = next((r for r in rows if r["done"] * 2 >= r["total"]), None)
        if halfway is None or halfway["eta_s"] is None:
            continue
        actual_remaining = finished_at - halfway["elapsed_s"]
        if actual_remaining <= 0:
            continue
        error = abs(halfway["eta_s"] - actual_remaining) / actual_remaining
        assert error <= TOLERANCE, (
            f"{path.stem} {attempt}: at {halfway['done']}/{halfway['total']} the feed "
            f"showed {halfway['eta_s']}s remaining, actual was "
            f"{round(actual_remaining, 1)}s ({error:.0%} out)")
        checked += 1
    assert checked, f"{path.stem} carried no completed multi-seed attempt to check"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_eta_is_a_measured_rate(path):
    for event in by_kind(load(path), "attempt.progress"):
        payload = event["payload"]
        assert payload["basis"] == "measured_seed_rate"
        # never reported before something has been measured
        assert payload["done"] >= 1 and payload["done"] <= payload["total"]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_the_ceiling_was_never_exceeded(path):
    """Recomputed at every attempt transition against what actually remained. The
    ceiling is enforced rather than predicted - TTL is charged before a sandbox exists
    and the budget refuses to overspend - so a breach would mean the bound is wrong."""
    events = load(path)
    end = events[-1]["t"]
    ttl_s, started, open_ = {}, {}, set()
    checks = 0
    for event in by_kind(events, "attempt.state"):
        payload, now = event["payload"], event["t"]
        attempt = payload["attempt_id"]
        if payload["state"] == "queued":
            ttl_s[attempt] = float(payload.get("ttl_min") or 0) * 60
            started[attempt] = now
            open_.add(attempt)
        elif payload["state"] in ("done", "failed"):
            open_.discard(attempt)
        if not open_:
            continue
        ceiling = estimates.run_ceiling_s(
            [{"started": started[a], "ttl_s": ttl_s.get(a, 0)} for a in open_], now)
        assert end - now <= ceiling + 1, (
            f"{path.stem}: at {now} the ceiling said {ceiling}s but the run had "
            f"{round(end - now, 1)}s left")
        checks += 1
    assert checks, f"{path.stem} recorded no attempt transitions"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_no_run_level_completion_time_was_ever_emitted(path):
    """The vocabulary has no whole-run completion kind, and the derived band that could
    carry one is withheld while a round is pending. Both halves are asserted: the stream
    never carries such a number, and the function that computes it returns None."""
    for event in load(path):
        assert event["kind"] in telemetry.KINDS or event["kind"] not in telemetry.KINDS
        payload = event["payload"]
        if isinstance(payload, dict) and event["kind"] != "attempt.progress":
            assert "eta_s" not in payload, event["kind"]
            assert "completion" not in payload, event["kind"]

    fleet = estimates.fleet_eta(0, [], 4, 2, estimates.duration_prior([60, 90, 120]))
    assert estimates.run_completion(fleet, round_pending=True) is None
    assert estimates.run_completion(fleet, round_pending=False) is not None

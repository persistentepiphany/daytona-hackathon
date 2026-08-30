"""Replay a recorded run through the real endpoint and check what a viewer would see.

Replay is not a test fixture. It is the way a finished run is watched, and the demo's
insurance policy, so it goes through the same server, the same frames and the same
reducers as a live run. These tests load streams recorded from live Daytona
(`fixtures/feed/`) into a ledger and assert that paced replay reproduces them in order,
and that every kind they carry has a reducer on the page.
"""

import http.client
import json
import re
import threading
import time
from pathlib import Path

import pytest

from repro import feed
from repro.orchestrator.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "fixtures" / "feed").glob("*.jsonl"))
RUN = "run-replay"


def load(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def seeded_ledger(tmp_path, events):
    """Replay needs a ledger, not a file: the endpoint reads rows, so a recorded stream
    is loaded back into one and served exactly as the run that produced it was."""
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    for event in events:
        ledger.db.execute(
            "INSERT INTO events (event_id, run_id, kind, payload, created_at)"
            " VALUES (?,?,?,?,?)",
            (f"evt-{event['id']:012d}", RUN, event["kind"],
             json.dumps(event["payload"], sort_keys=True), event["t"]))
    ledger.db.commit()
    return ledger


def drain(port, count, query="", timeout=25.0, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("GET", f"/events?run_id={RUN}{query}", headers=headers or {})
    resp = conn.getresponse()
    frames, buf = [], b""
    deadline = time.monotonic() + timeout
    while len(frames) < count and time.monotonic() < deadline:
        chunk = resp.read(1)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            block, _, buf = buf.partition(b"\n\n")
            text = block.decode()
            if text.startswith(":"):
                continue
            frame = {}
            for line in text.splitlines():
                if line.startswith("id: "):
                    frame["id"] = int(line[4:])
                elif line.startswith("data: "):
                    frame["data"] = json.loads(line[6:])
                elif line.startswith("event: "):
                    frame["event"] = line[7:]
            frames.append(frame)
    conn.close()
    return frames


@pytest.fixture
def served(tmp_path, request):
    ledger = seeded_ledger(tmp_path, request.param)
    server = feed.make_server(str(tmp_path / "ledger.db"), str(tmp_path), port=0,
                              bus=ledger.bus, default_run=RUN)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], request.param
    server.shutdown()
    server.server_close()


def _params():
    return [pytest.param(load(p), id=p.stem) for p in FIXTURES]


def test_there_are_recorded_runs_to_replay():
    assert FIXTURES, "no recorded streams in fixtures/feed/"


@pytest.mark.parametrize("served", _params(), indirect=True)
def test_paced_replay_reproduces_the_run_in_order(served):
    port, events = served
    frames = drain(port, len(events), query="&replay=paced&speed=500")
    ledger_frames = [f for f in frames if "id" in f]
    assert [f["data"]["kind"] for f in ledger_frames] == [e["kind"] for e in events]
    assert [f["id"] for f in ledger_frames] == sorted(f["id"] for f in ledger_frames)
    assert ledger_frames[0]["data"]["payload"] == events[0]["payload"]


@pytest.mark.parametrize("served", _params(), indirect=True)
def test_every_recorded_kind_has_a_reducer_on_the_page(served):
    """A kind with no reducer renders as nothing at all, which in a live demo looks
    exactly like a stalled run."""
    _, events = served
    page = feed.PAGE
    reducers = set(re.findall(r'"([a-z]+\.[a-z]+)":\s*p\s*=>', page))
    from repro import telemetry
    for kind in {e["kind"] for e in events} & telemetry.KINDS:
        assert kind in reducers, f"the page has no reducer for {kind}"


@pytest.mark.parametrize("served", _params(), indirect=True)
def test_replay_can_be_resumed_midway_like_a_live_stream(served):
    port, events = served
    half = max(1, len(events) // 2)
    first = [f for f in drain(port, half) if "id" in f]
    resumed = [f for f in drain(port, len(events) - len(first),
                                headers={"Last-Event-ID": str(first[-1]["id"])})
               if "id" in f]
    ids = [f["id"] for f in first] + [f["id"] for f in resumed]
    assert len(ids) == len(set(ids)), "a reconnecting viewer saw an event twice"
    assert ids == sorted(ids)
    assert ids == list(range(ids[0], ids[0] + len(ids))), "the resume skipped an event"
    assert len(ids) == len(events)


def test_the_archaeology_recording_shows_agents_building():
    """The live recording this replays is a real S0 build: what the feed must show is
    the actions taken, the files written with their diffs, and the results."""
    path = next((p for p in FIXTURES if "archaeology" in p.stem), None)
    if path is None:
        pytest.skip("no archaeology recording present")
    events = load(path)
    kinds = {e["kind"] for e in events}
    assert {"agent.action", "agent.patch", "agent.observation", "gate.changed",
            "budget.tick", "run.done"} <= kinds
    patches = [e["payload"] for e in events if e["kind"] == "agent.patch"]
    assert patches and all(p["added"] > 0 and p["hunk"] for p in patches)
    assert all(p["evidence_path"] is None or p["evidence_path"].startswith("_patches/")
               for p in patches)

"""The SSE transport: framing, the catch-up/live handover, resume, and paced replay."""

import http.client
import json
import threading
import time

import pytest

from repro import feed
from repro.orchestrator.ledger import Ledger

RUN = "run-feed"


@pytest.fixture
def served(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    ledger.bus.enabled = True  # the feed is opt-in; these tests are the opting in
    server = feed.make_server(str(tmp_path / "ledger.db"), str(tmp_path), port=0,
                              bus=ledger.bus, default_run=RUN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield ledger, server.server_address[1]
    server.shutdown()
    server.server_close()


def read_frames(port: int, count: int, headers: dict | None = None,
                query: str = "", timeout: float = 8.0):
    """Pull `count` data frames off the stream, then hang up the way a closed tab does."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("GET", f"/events?run_id={RUN}{query}", headers=headers or {})
    resp = conn.getresponse()
    frames, comments, buf = [], [], b""
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
                comments.append(text)
                continue
            frame = {"raw": text}
            for line in text.splitlines():
                if line.startswith("id: "):
                    frame["id"] = int(line[4:])
                elif line.startswith("data: "):
                    frame["data"] = json.loads(line[6:])
                elif line.startswith("event: "):
                    frame["event"] = line[7:]
            frames.append(frame)
    return resp, frames, comments, conn


def seed_events(ledger, n, start=0):
    for i in range(start, start + n):
        ledger.bus.emit(RUN, "budget.tick", {"kind": "sandbox_minutes", "spent": i,
                                             "ceiling": 100})


# --- framing ---------------------------------------------------------------

def test_stream_is_event_stream_with_no_content_length(served):
    ledger, port = served
    seed_events(ledger, 3)
    resp, frames, _, conn = read_frames(port, 3)
    assert resp.getheader("Content-Type") == "text/event-stream"
    # a Content-Length would terminate the body; this one never ends
    assert resp.getheader("Content-Length") is None
    assert resp.getheader("Cache-Control") == "no-cache"
    conn.close()


def test_frames_carry_the_ledger_rowid_as_the_sse_id(served):
    ledger, port = served
    seed_events(ledger, 3)
    _, frames, _, conn = read_frames(port, 3)
    ledger_ids = [r["id"] for r in ledger.events_after(RUN, 0)]
    assert [f["id"] for f in frames] == ledger_ids[:3]
    assert frames[0]["data"]["kind"] == "budget.tick"
    conn.close()


def test_estimate_arrives_as_a_named_frame_outside_the_vocabulary(served):
    """The event vocabulary is closed, so derived timing is delivered as its own SSE
    event and never appended to the ledger."""
    ledger, port = served
    seed_events(ledger, 1)
    _, frames, _, conn = read_frames(port, 2)
    named = [f for f in frames if f.get("event") == "estimate"]
    assert named and "ceiling_s" in named[0]["data"]
    assert [r["kind"] for r in ledger.events_for(RUN)] == ["budget.tick"]
    conn.close()


# --- catch-up and resume ---------------------------------------------------

def test_after_cursor_replays_only_the_suffix(served):
    ledger, port = served
    seed_events(ledger, 6)
    ids = [r["id"] for r in ledger.events_after(RUN, 0)]
    _, frames, _, conn = read_frames(port, 3, query=f"&after={ids[2]}")
    assert [f["id"] for f in frames if "id" in f][:3] == ids[3:6]
    conn.close()


def test_last_event_id_resumes_with_no_gap_and_no_duplicate(served):
    """Acceptance: kill the browser mid-stream and reopen it. Asserted on event ids,
    because 'looks continuous' is not the same claim."""
    ledger, port = served
    seed_events(ledger, 10)
    _, first, _, conn = read_frames(port, 4)
    conn.close()
    seen = [f["id"] for f in first if "id" in f]

    _, second, _, conn2 = read_frames(port, 6, headers={"Last-Event-ID": str(seen[-1])})
    conn2.close()
    resumed = [f["id"] for f in second if "id" in f]

    assert set(seen) & set(resumed) == set()                 # nothing delivered twice
    union = seen + resumed
    assert union == sorted(union)
    assert union == list(range(union[0], union[0] + len(union)))  # nothing skipped


def test_last_event_id_wins_over_the_after_parameter(served):
    ledger, port = served
    seed_events(ledger, 8)
    ids = [r["id"] for r in ledger.events_after(RUN, 0)]
    _, frames, _, conn = read_frames(port, 2, headers={"Last-Event-ID": str(ids[5])},
                                     query="&after=0")
    assert [f["id"] for f in frames if "id" in f][:2] == ids[6:8]
    conn.close()


def test_events_emitted_during_catch_up_are_delivered_exactly_once(served):
    """The handover is the risky moment: the subscription opens before the catch-up
    query runs, so anything emitted mid-replay is queued and then id-filtered."""
    ledger, port = served
    seed_events(ledger, 40)

    def writer():
        time.sleep(0.05)
        seed_events(ledger, 20, start=40)

    threading.Thread(target=writer, daemon=True).start()
    _, frames, _, conn = read_frames(port, 60)
    conn.close()
    ids = [f["id"] for f in frames if "id" in f]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)
    assert ids == list(range(ids[0], ids[0] + len(ids)))


# --- replay and keepalive --------------------------------------------------

def test_paced_replay_preserves_order_and_content(served):
    ledger, port = served
    seed_events(ledger, 8)
    _, frames, _, conn = read_frames(port, 8, query="&replay=paced&speed=1000")
    conn.close()
    spends = [f["data"]["payload"]["spent"] for f in frames if "id" in f]
    assert spends == list(range(8))


def test_paced_replay_actually_sleeps_between_recorded_gaps(served, monkeypatch):
    """Paced replay is the demo's insurance policy, so it has to reproduce the run's
    rhythm rather than dumping the backlog."""
    ledger, port = served
    for i in range(4):
        ledger.bus.emit(RUN, "budget.tick", {"kind": "x", "spent": i, "ceiling": 1})
        time.sleep(0.12)
    t0 = time.monotonic()
    _, frames, _, conn = read_frames(port, 4, query="&replay=paced&speed=2")
    elapsed = time.monotonic() - t0
    conn.close()
    assert len([f for f in frames if "id" in f]) == 4
    assert elapsed >= 0.1   # three ~60ms gaps at speed 2, versus ~0 unpaced


def test_ping_keeps_an_idle_stream_open(served, monkeypatch):
    """An idle run must not look like a dead connection to the browser or to any proxy
    between them, so the stream comments on itself while nothing is happening."""
    monkeypatch.setattr(feed, "PING_SECONDS", 0.05)
    ledger, port = served
    seed_events(ledger, 1)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", f"/events?run_id={RUN}")
    resp = conn.getresponse()
    buf, deadline = b"", time.monotonic() + 1.5
    while time.monotonic() < deadline:
        buf += resp.read(1)
    conn.close()
    text = buf.decode(errors="replace")
    assert ": live" in text      # the catch-up/live handover marker
    assert ": ping" in text      # and the keepalive behind it


def test_a_run_id_is_required_when_the_server_has_no_default(tmp_path):
    ledger = Ledger(tmp_path / "other.db")
    ledger.create_run(RUN, paper_hash="p" * 64, prereg_hash="h" * 64)
    ledger.bus.enabled = True
    server = feed.make_server(str(tmp_path / "other.db"), str(tmp_path), port=0,
                              bus=ledger.bus)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.request("GET", "/events")
        assert conn.getresponse().status == 400
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_page_is_served_and_wires_an_eventsource(served):
    ledger, port = served
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/")
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    assert resp.status == 200
    assert "EventSource" in body and "attempt.progress" in body

"""Live run feed: one SSE endpoint and one static page, served by the orchestrator.

The endpoint replays the ledger's events from a cursor, then switches to the in-memory
bus without a gap or a duplicate — the SSE `id:` field is the event's ledger rowid, so a
browser that reconnects resumes exactly where it stopped. That property is what makes
`?replay=paced` more than a test hack: a finished run replays through the same code path
as a live one, which is the demo's insurance policy.

Runs in the orchestrator's own process on 127.0.0.1: one port, no IPC, no auth, no
framework, no websockets. It also works out-of-process against a ledger file, in which
case the live tail comes from polling rather than the bus.
"""

import json
import queue
import sqlite3
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import dashboard, estimates

PING_SECONDS = 15
POLL_SECONDS = 0.25
ESTIMATE_SECONDS = 10
MAX_SUBSCRIBERS = 8
MAX_PACED_SLEEP = 2.0
PACED_BUDGET_SECONDS = 600.0


def _frame(event_id: int, kind: str, payload: dict, t: float) -> bytes:
    body = json.dumps({"id": event_id, "kind": kind, "payload": payload, "t": t})
    return f"id: {event_id}\ndata: {body}\n\n".encode()


def _named(name: str, data: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


# ---------------------------------------------------------------------------
# estimates over the ledger (derived state, never an event row)
# ---------------------------------------------------------------------------

def estimate_frame(db: sqlite3.Connection, run_id: str, width: int,
                   default_attempt_s: float, planned: int | None = None,
                   round_pending: bool = False, round_k: int = 0,
                   round_cap: int = 0) -> dict:
    """Fleet band + hard ceiling, recomputed from the attempts table.

    Deliberately not an event kind: the vocabulary is closed, and a derived number that
    changes on every tick has no business being appended to an append-only ledger.
    """
    now = time.time()
    rows = db.execute(
        "SELECT attempt_id, started, ended, exit, cost_est FROM attempts WHERE run_id=?",
        (run_id,)).fetchall()
    done = [dict(r) for r in rows if r["ended"] is not None]
    open_ = [dict(r) for r in rows if r["ended"] is None]
    durations = [r["ended"] - r["started"] for r in done
                 if r["ended"] and r["started"]]
    history = [r["d"] for r in db.execute(
        "SELECT (a.ended - a.started) AS d FROM attempts a JOIN runs r ON r.run_id=a.run_id"
        " WHERE a.run_id!=? AND a.ended IS NOT NULL AND r.paper_hash="
        " (SELECT paper_hash FROM runs WHERE run_id=?)", (run_id, run_id)).fetchall()
       if r["d"] and r["d"] > 0]

    prior = estimates.duration_prior(durations, history, default_attempt_s)
    running = [{"started": r["started"], "ttl_s": (r["cost_est"] or 0) * 60}
               for r in open_]
    pending = max(0, (planned or 0) - len(done) - len(open_))
    fleet = estimates.fleet_eta(now, running, pending, width, prior)
    ceiling = estimates.run_ceiling_s(running, now, rounds_remaining=0)
    return {
        "fleet": fleet,
        "ceiling_s": ceiling,
        "completion": estimates.run_completion(fleet, round_pending),
        "round": ({"k": round_k, "cap": round_cap, "label": estimates.round_label(round_k, round_cap)}
                  if round_cap else None),
        "attempts": {"done": len(done), "running": len(open_), "pending": pending},
    }


# ---------------------------------------------------------------------------
# the SSE stream
# ---------------------------------------------------------------------------

def stream(write, db: sqlite3.Connection, bus, run_id: str, after: int = 0,
           paced: bool = False, speed: float = 1.0, width: int = 2,
           default_attempt_s: float = 900.0, planned: int | None = None,
           stop_when_idle: float | None = None) -> None:
    """Catch up from `after`, then follow. `write` raises on client disconnect, which
    is the normal way this returns."""
    q = None
    if bus is not None:
        # subscribe BEFORE the catch-up query: anything emitted during replay lands in
        # the queue and is filtered by id, so the handover loses and repeats nothing
        q = bus.subscribe(run_id)
    last_id = int(after)
    try:
        last_id = _catch_up(write, db, run_id, last_id, paced, speed)
        write(b": live\n\n")
        _follow(write, db, bus, q, run_id, last_id, width, default_attempt_s, planned,
                stop_when_idle)
    finally:
        if q is not None and bus is not None:
            bus.unsubscribe(run_id, q)


def _catch_up(write, db, run_id, last_id, paced, speed) -> int:
    rows = db.execute(
        "SELECT rowid AS id, kind, payload, created_at FROM events"
        " WHERE run_id=? AND rowid>? ORDER BY rowid", (run_id, last_id)).fetchall()
    budget = PACED_BUDGET_SECONDS
    previous = None
    for row in rows:
        if paced and previous is not None and budget > 0:
            delta = min((row["created_at"] - previous) / speed, MAX_PACED_SLEEP)
            if delta > 0:
                time.sleep(min(delta, budget))
                budget -= delta
        previous = row["created_at"]
        write(_frame(row["id"], row["kind"], json.loads(row["payload"]),
                     row["created_at"]))
        last_id = row["id"]
    return last_id


def _follow(write, db, bus, q, run_id, last_id, width, default_attempt_s, planned,
            stop_when_idle) -> None:
    last_ping = last_estimate = time.monotonic()
    idle_since = time.monotonic()
    write(_named("estimate", estimate_frame(db, run_id, width, default_attempt_s, planned)))
    while True:
        got = False
        if q is not None:
            while True:
                try:
                    frame = q.get_nowait()
                except queue.Empty:
                    break
                if frame["id"] > last_id:
                    write(_frame(frame["id"], frame["kind"], frame["payload"], frame["t"]))
                    last_id = frame["id"]
                    got = True
        # also poll: the same page must work against a ledger written by another
        # process, which is how a finished run is replayed
        for row in db.execute(
                "SELECT rowid AS id, kind, payload, created_at FROM events"
                " WHERE run_id=? AND rowid>? ORDER BY rowid LIMIT 500",
                (run_id, last_id)).fetchall():
            write(_frame(row["id"], row["kind"], json.loads(row["payload"]),
                         row["created_at"]))
            last_id = row["id"]
            got = True
        now = time.monotonic()
        if got:
            idle_since = now
        if now - last_estimate >= ESTIMATE_SECONDS or got:
            write(_named("estimate",
                         estimate_frame(db, run_id, width, default_attempt_s, planned)))
            last_estimate = now
        if now - last_ping >= PING_SECONDS:
            write(b": ping\n\n")
            last_ping = now
        if stop_when_idle is not None and now - idle_since >= stop_when_idle:
            return
        time.sleep(POLL_SECONDS)


def iter_frames(ledger_path: str, run_id: str, after: int = 0, paced: bool = False,
                speed: float = 1.0, width: int = 2, default_attempt_s: float = 900.0,
                planned: int | None = None, idle_timeout: float | None = None):
    """The same stream as the stdlib handler, yielded rather than written.

    Lets an ASGI app (the run API) serve the identical bytes without duplicating the
    catch-up, hand-over and replay logic. With no bus it tails the ledger by polling,
    which is what watching another process's run requires anyway.
    """
    import threading

    frames: queue.Queue = queue.Queue(maxsize=1024)
    done = object()

    def run():
        db = sqlite3.connect(ledger_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            stream(frames.put, db, None, run_id, after, paced, speed, width,
                   default_attempt_s, planned, stop_when_idle=idle_timeout)
        except Exception:
            pass  # a dropped viewer is not an error
        finally:
            db.close()
            frames.put(done)

    threading.Thread(target=run, name=f"feed-{run_id}", daemon=True).start()
    while True:
        item = frames.get()
        if item is done:
            return
        yield item


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

def make_feed_handler(ledger_path: str, evidence_root: str, bus=None,
                      default_run: str | None = None, width: int = 2,
                      default_attempt_s: float = 900.0, planned: int | None = None,
                      force_replay: str | None = None, force_speed: float = 1.0):
    Base = dashboard.make_handler(ledger_path, evidence_root)
    live = {"subscribers": 0}

    class FeedHandler(Base):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/events":
                return self._events(parse_qs(url.query))
            if url.path in ("/", "/live"):
                return self._send(PAGE.encode(), "text/html; charset=utf-8")
            if url.path == "/ledger":
                return super().do_GET()
            return super().do_GET()

        def _events(self, params):
            run_id = (params.get("run_id") or [default_run or ""])[0]
            if not run_id:
                return self._send(b"run_id required", "text/plain", 400)
            if live["subscribers"] >= MAX_SUBSCRIBERS:
                return self._send(b"too many feed subscribers", "text/plain", 503)
            after = self._resume_from(params)
            paced = (params.get("replay") or [force_replay or ""])[0] == "paced"
            try:
                speed = float((params.get("speed") or [force_speed])[0])
            except (TypeError, ValueError):
                speed = 1.0
            speed = max(1.0, speed)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()  # deliberately no Content-Length: this body never ends

            db = sqlite3.connect(ledger_path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            live["subscribers"] += 1
            try:
                stream(self._write, db, bus, run_id, after, paced, speed, width,
                       default_attempt_s, planned)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # the viewer closed the tab; the run does not care
            finally:
                live["subscribers"] -= 1
                db.close()

        def _resume_from(self, params) -> int:
            """Last-Event-ID wins over ?after=: the browser knows better than the URL
            what it has already rendered."""
            for value in (self.headers.get("Last-Event-ID"),
                          (params.get("after") or ["0"])[0]):
                try:
                    if value not in (None, ""):
                        return int(value)
                except (TypeError, ValueError):
                    continue
            return 0

        def _write(self, data: bytes) -> None:
            self.wfile.write(data)
            self.wfile.flush()

    return FeedHandler


def serve(ledger_path: str, evidence_root: str, port: int = 8700, **kw) -> None:
    server = make_server(ledger_path, evidence_root, port, **kw)
    print(f"live feed on http://127.0.0.1:{port} (ledger: {ledger_path})")
    server.serve_forever()


def make_server(ledger_path: str, evidence_root: str, port: int = 8700, **kw):
    handler = make_feed_handler(str(ledger_path), str(evidence_root), **kw)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def serve_background(ledger_path, evidence_root, port: int = 8700, **kw):
    """Start the feed on a daemon thread inside the orchestrator process, so a live run
    and the page watching it share one process and one bus."""
    import threading

    server = make_server(str(ledger_path), str(evidence_root), port, **kw)
    thread = threading.Thread(target=server.serve_forever, name="feed", daemon=True)
    thread.start()
    return server


PAGE = (Path(__file__).parent / "feed_page.html").read_text()

"""The event bus: one emit() every run event passes through, redaction included.

Two writes per event, one call: the row is appended to the ledger's `events` table
(the durable record) and pushed to in-memory subscriber queues (the live feed, sub-100ms).
Redaction happens here and only here — no call site is trusted to scrub its own payload,
so a credential can only reach the table or the browser by getting past this module.

Events carry display tails, never canonical artifacts: a patch event carries the head of
a hunk plus the path of the full diff on disk, an observation carries an output tail. The
evidence files remain the only source of truth.

Redaction is deliberately NOT behind the `enabled` flag. The flag gates the new event
kinds, the fan-out and the sandbox tap; it must never gate scrubbing, or a run with the
feed switched off would write a leakier events table than one with it on.
"""

import json
import queue
import re
import threading
import time

# closed vocabulary: emit() refuses anything else. Legacy ledger kinds (sandbox_created,
# manifest_frozen, ...) predate this feature and pass through the _legacy path unchecked.
KINDS = frozenset({
    "agent.action",
    "agent.patch",
    "agent.observation",
    "log.chunk",
    "attempt.state",
    "attempt.progress",
    "gate.changed",
    "verdict.emitted",
    "budget.tick",
    "run.done",
})

REDACTED = "[REDACTED]"

# dict keys whose value is always a credential. Deliberately exact: a prefix rule would
# swallow `config_key`, which is load-bearing in every experiment manifest.
SECRET_KEYS = frozenset({
    "api_key", "apikey", "api-key", "access_token", "auth_token", "token",
    "secret", "client_secret", "password", "passwd", "authorization",
})

_TOKEN_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"ghp_[A-Za-z0-9]{12,}"),
    re.compile(r"gho_[A-Za-z0-9]{12,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{12,}"),
    re.compile(r"dtn_[A-Za-z0-9]{12,}"),
    # Authorization headers: the scheme is not the secret, what follows it is
    re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._\-+/=]{8,}"),
)

# key=value / key: value in free text.
#
# Two alternations, because one rule cannot do both jobs. The unambiguous words allow a
# leading identifier segment, so `PARALLEL_API_KEY=`, `ANTHROPIC_API_KEY=` and
# `ZAI_API=` are caught - a prefix-blind rule missed every real environment variable in
# this repo. A bare `key` keeps the lookbehind, so `config_key=data.shuffle_labels` and
# `--set models.C2.params.n_estimators=10` survive intact; those are load-bearing in
# every manifest the ledger replays from.
_ASSIGNMENT = re.compile(
    r"(?i)("
    r"[\w\-]*(?:api[_\-]?key|apikey|_api|access[_\-]?token|auth[_\-]?token"
    r"|token|secret|password|passwd)"
    r"|(?<![\w\-.])key"
    r")(\s*[=:]\s*)([\"']?)(?!\[REDACTED\])([^\s\"'&;,)]{6,})"
)


class TelemetryError(RuntimeError):
    pass


def redact(value):
    """Recursively scrub credentials from any payload. Structure and non-string
    scalars are preserved exactly; only string values change."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {k: (REDACTED if str(k).lower() in SECRET_KEYS and v not in (None, "")
                    else redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _redact_text(text: str) -> str:
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", text)


def enabled_default() -> bool:
    """The feed is opt-in: off unless REPRO_TELEMETRY=1 or a policy turns it on.

    Deliberately delegated to the policy module rather than re-reading the variable
    here, so there is one definition of the switch and the policy key cannot become
    decoration.
    """
    from .orchestrator.policy import telemetry_enabled

    return telemetry_enabled()


class Bus:
    """Dual write. One instance per ledger; the ledger constructs its own so that
    `log_event` and `emit` cannot diverge into two redaction sites."""

    def __init__(self, ledger, enabled: bool | None = None):
        self.ledger = ledger
        self.enabled = enabled_default() if enabled is None else enabled
        self._subs: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()

    # subscribers ---------------------------------------------------------
    def subscribe(self, run_id: str, maxsize: int = 4096) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subs.get(run_id) or []
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subs.pop(run_id, None)

    def subscriber_count(self, run_id: str) -> int:
        with self._lock:
            return len(self._subs.get(run_id) or [])

    # the choke point -----------------------------------------------------
    def emit(self, run_id: str, kind: str, payload: dict,
             _legacy: bool = False) -> tuple[str | None, int]:
        """Redact, append to the events table, fan out. Returns (event_id, row_id)."""
        if not _legacy:
            if kind not in KINDS:
                raise TelemetryError(f"event kind {kind!r} is outside the closed vocabulary")
            if not self.enabled:
                return None, 0
        clean = redact(payload)
        # the row id and the fan-out must be atomic together: a subscriber advances a
        # strictly monotonic cursor, so delivering 11 before 10 loses 10 for good
        with self.ledger.lock:
            event_id, row_id, created_at = self.ledger._insert_event(run_id, kind, clean)
            if self.enabled:
                self._fanout(run_id, {"id": row_id, "event_id": event_id, "kind": kind,
                                      "payload": clean, "t": created_at})
        return event_id, row_id

    def _fanout(self, run_id: str, frame: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(run_id) or [])
        for q in subs:
            try:
                q.put_nowait(frame)
            except queue.Full:
                try:  # drop the oldest: a slow browser must never stall the run
                    q.get_nowait()
                    q.put_nowait(frame)
                except (queue.Empty, queue.Full):
                    pass


# ---------------------------------------------------------------------------
# the action tap: agent.action / agent.patch / agent.observation
# ---------------------------------------------------------------------------

_TAP = None
_local = threading.local()


def set_tap(tap) -> None:
    """Arm the choke-point producer for this process. One run per process."""
    global _TAP
    _TAP = tap


def action_tap():
    return _TAP


def _inside() -> bool:
    return getattr(_local, "depth", 0) > 0


class ActionTap:
    """Emits the three agent events around one applied action.

    Previous file state is mirrored host-side (what this run last wrote through the
    tap), so a patch costs no extra sandbox round-trip and the executor behaves
    identically whether or not the feed is on. A first write to a path baked into S0
    therefore reads as all-added, which is the honest statement of what this run knows.
    """

    HUNK_LIMIT = 2500
    TAIL_LIMIT = 1200

    def __init__(self, bus: Bus, run_id: str, role: str = "implementer",
                 evidence_root=None):
        self.bus = bus
        self.run_id = run_id
        self.role = role
        self.evidence_root = evidence_root
        self._files: dict[str, str] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def around(self, action: dict, call):
        """Run one action between its before/after events. A raising action still
        reports an observation — a failure is the most interesting thing in the feed."""
        _local.depth = getattr(_local, "depth", 0) + 1
        try:
            self.before(action)
            try:
                result = call()
            except Exception as e:
                self.after(action, 1, f"{type(e).__name__}: {e}")
                raise
            self.after(action, getattr(result, "exit_code", 0),
                       getattr(result, "output", "") or "")
            return result
        finally:
            _local.depth -= 1

    def before(self, action: dict) -> None:
        kind = action.get("action")
        self.bus.emit(self.run_id, "agent.action", {
            "role": self.role, "type": kind, "summary": _summarize(action),
        })
        if kind == "write":
            self._patch(action)

    def after(self, action: dict, exit_code: int, output: str) -> None:
        self.bus.emit(self.run_id, "agent.observation", {
            "role": self.role, "type": action.get("action"), "exit": int(exit_code),
            "tail": (output or "")[-self.TAIL_LIMIT:],
        })

    def _patch(self, action: dict) -> None:
        import difflib

        path = action["path"]
        new = action["content"]
        old = self._files.get(path, "")
        diff = list(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
        ))
        added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
        text = "".join(diff)
        base = "session" if path in self._files else "empty"
        self._files[path] = new
        self.bus.emit(self.run_id, "agent.patch", {
            "role": self.role, "path": path, "added": added, "removed": removed,
            # `base` keeps the card honest: a first write to a path baked into S0 has no
            # prior state this run has seen, so it renders as a full-file add
            "base": base,
            "hunk": text[:self.HUNK_LIMIT], "evidence_path": self._save(path, text),
        })

    def _save(self, path: str, text: str) -> str | None:
        """The full diff is an artifact, not an event: it goes to evidence and the
        event references it by path."""
        if not self.evidence_root or not text:
            return None
        from pathlib import Path

        with self._lock:
            self._seq += 1
            seq = self._seq
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-")[:60] or "file"
        out = Path(self.evidence_root) / "_patches"
        out.mkdir(parents=True, exist_ok=True)
        name = f"{seq:04d}-{slug}.diff"
        (out / name).write_text(text)
        return f"_patches/{name}"


def _summarize(action: dict) -> str:
    kind = action.get("action")
    if kind == "run":
        return str(action.get("cmd", ""))[:200]
    if kind == "write":
        return f"write {action.get('path', '')}"[:200]
    return str(action.get("objective", ""))[:200]


class _TappedSession:
    """Wraps an archaeology session so callers that build the environment directly —
    the calibration recipe does — produce the same agent events as the choke point.
    The depth guard means an action arriving through apply_action is never counted twice.
    """

    def __init__(self, session, role: str = "implementer"):
        self._session = session
        self._role = role

    def sh(self, cmd: str, **kw):
        tap = action_tap()
        if tap is None or _inside():
            return self._session.sh(cmd, **kw)
        return tap.around({"action": "run", "cmd": cmd},
                          lambda: self._session.sh(cmd, **kw))

    def put_file(self, relpath: str, content: str):
        tap = action_tap()
        if tap is None or _inside():
            return self._session.put_file(relpath, content)
        return tap.around({"action": "write", "path": relpath, "content": content},
                          lambda: self._session.put_file(relpath, content))

    def __getattr__(self, name):
        return getattr(self._session, name)


def tapped_session(session, role: str = "implementer"):
    return _TappedSession(session, role)


# ---------------------------------------------------------------------------
# log coalescer: raw sandbox output -> log.chunk + attempt.progress
# ---------------------------------------------------------------------------

_PROGRESS = re.compile(r"::progress\s+(\d+)\s*/\s*(\d+)")
# Fallback for an S0 frozen before the progress side channel existed: the runner has
# always announced each seed. Note it announces a seed *starting*, so the number
# finished is one less than the number seen - counting announcements as completions
# would report work that has not happened.
_SEED_LINE = re.compile(r"^\[runner\].*\bseed=(\d+)", re.MULTILINE)

PROGRESS_STREAM = "progress"


class LogCoalescer:
    """Buffers sandbox output per (attempt, stream) and flushes on 300ms or 2KB.

    Without coalescing a chatty training run emits an event per line and drowns both
    the events table and the browser; with it the feed stays near one frame per stream
    per 300ms while keeping latency a viewer can perceive.

    feed() only appends. Every ledger write happens on the flusher thread, because the
    SDK's log callbacks run on a websocket pump and blocking inside one drops the
    socket; the flusher wakes every 100ms, so the 2KB trigger still fires promptly.

    Two rules protect the run from its own telemetry. Attempts belonging to the
    held-out annex stream byte counts only, never text: a held-out experiment's stdout
    carries its observed metric, and the feed must not become a side channel around the
    seal. And a per-attempt byte cap stops a runaway log from inflating the ledger.
    """

    FLUSH_SECONDS = 0.3
    FLUSH_BYTES = 2048
    MAX_BYTES_PER_ATTEMPT = 2 * 1024 * 1024

    def __init__(self, bus: Bus, run_id: str, held_out: set[str] | None = None):
        self.bus = bus
        self.run_id = run_id
        self.held_out = set(held_out or ())  # attempt ids whose text must not stream
        self._buf: dict[tuple[str, str], list[str]] = {}
        self._size: dict[tuple[str, str], int] = {}
        self._deadline: dict[tuple[str, str], float] = {}
        self._totals: dict[str, int] = {}
        self._t0: dict[str, float] = {}
        self._done: dict[str, int] = {}
        self._marked: set[str] = set()      # attempts that speak the ::progress channel
        self._seeds: dict[str, set[str]] = {}   # distinct seeds seen via the fallback
        self._sent: dict[str, int] = {}
        self._capped: set[str] = set()
        self._cap_notice: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="log-coalescer",
                                        daemon=True)
        self._thread.start()

    def track(self, attempt_id: str, total_seeds: int, held_out: bool = False) -> None:
        with self._lock:
            self._totals[attempt_id] = int(total_seeds)
            self._t0.setdefault(attempt_id, time.monotonic())
            self._done.setdefault(attempt_id, 0)
            if held_out:
                self.held_out.add(attempt_id)

    def feed(self, attempt_id: str, stream: str, text: str) -> None:
        """One callback's worth of output. `stream` is stdout, stderr, or progress;
        the progress side channel is parsed and never echoed as a log chunk."""
        if not text:
            return
        self._progress(attempt_id, text)
        if stream == PROGRESS_STREAM:
            return
        if attempt_id in self.held_out:
            self.bus.emit(self.run_id, "log.chunk", {
                "attempt_id": attempt_id, "stream": stream,
                "bytes": len(text), "suppressed": True,
            })
            return
        key = (attempt_id, stream)
        with self._lock:
            if attempt_id in self._capped:
                return
            sent = self._sent.get(attempt_id, 0) + len(text)
            self._sent[attempt_id] = sent
            self._buf.setdefault(key, []).append(text)
            self._size[key] = self._size.get(key, 0) + len(text)
            self._deadline.setdefault(key, time.monotonic() + self.FLUSH_SECONDS)
            if sent >= self.MAX_BYTES_PER_ATTEMPT:
                self._capped.add(attempt_id)
                self._cap_notice.add(attempt_id)

    def flush(self, key: tuple[str, str] | None = None) -> None:
        with self._lock:
            keys = [key] if key is not None else list(self._buf)
            pending = []
            for k in keys:
                chunk = "".join(self._buf.pop(k, []))
                self._size.pop(k, None)
                self._deadline.pop(k, None)
                if chunk:
                    pending.append((k, chunk))
        for (attempt_id, stream), chunk in pending:
            self.bus.emit(self.run_id, "log.chunk", {
                "attempt_id": attempt_id, "stream": stream, "text": chunk,
            })

    def _progress(self, attempt_id: str, text: str) -> None:
        """`::progress k/n` from the runner's side channel; the per-seed runner line is
        the fallback for an S0 frozen before that channel existed.

        The two must not both count. Once an attempt has spoken the explicit channel it
        is authoritative, and the fallback is ignored for the rest of the attempt -
        otherwise the runner's own stdout races the side channel and the count runs
        ahead of the work. The fallback itself counts distinct seeds, because the runner
        prints more than one line per seed.
        """
        with self._lock:
            total = self._totals.get(attempt_id)
            t0 = self._t0.get(attempt_id)
            prior = self._done.get(attempt_id, 0)
            marked = attempt_id in self._marked
        if not total or t0 is None:
            return
        done = prior
        marks = _PROGRESS.findall(text)
        if marks:
            done = max(done, int(marks[-1][0]))
            total = int(marks[-1][1]) or total
            marked = True
        elif not marked:
            seeds = set(_SEED_LINE.findall(text))
            if seeds:
                with self._lock:
                    known = self._seeds.setdefault(attempt_id, set())
                    known |= seeds
                    # the most recently announced seed is still running
                    done = max(0, len(known) - 1)
        done = min(done, total)
        if done <= prior:
            if marks:
                with self._lock:
                    self._marked.add(attempt_id)
            return
        with self._lock:
            self._done[attempt_id] = done
            self._totals[attempt_id] = total
            if marks:
                self._marked.add(attempt_id)
        elapsed = time.monotonic() - t0
        # measured: this attempt's own observed seed rate extrapolated over the seeds
        # it has left. Never a prior, never a model of a different attempt.
        eta = round(elapsed / done * (total - done), 1) if done else None
        self.bus.emit(self.run_id, "attempt.progress", {
            "attempt_id": attempt_id, "done": done, "total": total,
            "eta_s": eta, "elapsed_s": round(elapsed, 1), "basis": "measured_seed_rate",
        })

    def _loop(self) -> None:
        while not self._stop.wait(self.FLUSH_SECONDS / 3):
            self._tick()

    def _tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            due = [k for k, deadline in self._deadline.items()
                   if deadline <= now or self._size.get(k, 0) >= self.FLUSH_BYTES]
            notices = list(self._cap_notice)
            self._cap_notice.clear()
        for k in due:
            self.flush(k)
        for attempt_id in notices:
            self.bus.emit(self.run_id, "log.chunk", {
                "attempt_id": attempt_id, "stream": "stdout", "text": "",
                "truncated": True,
            })

    def close(self) -> None:
        """Flush what is left before the caller marks the attempt finished, so the last
        lines of a run are never the ones the viewer does not get."""
        self._stop.set()
        self._thread.join(timeout=2)
        self._tick()
        self.flush()

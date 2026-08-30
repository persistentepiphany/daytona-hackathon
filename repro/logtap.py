"""Follows a running sandbox's output without touching how the experiment runs.

The executor's command line is untouched: it is still
`bash runner.sh E001 > stdout.log 2>&1` through a synchronous exec, producing exactly
the bytes it produced before this feature existed. The tap runs alongside it as a
separate session tailing two files — the evidence log, and the progress side channel the
runner writes when asked. Nothing the tap does is hashed into evidence, so a run with
the feed on and a run with it off leave identical artifacts behind.

A tap must never be able to fail a run: every follower body swallows its exceptions and
the tap is closed before the sandbox is stopped.
"""

import threading

from .telemetry import PROGRESS_STREAM, LogCoalescer

WORK = "/home/daytona/work"
PROGRESS_FILE = "progress.jsonl"
MARKER_FILE = ".repro_progress"

# -n +1 starts at the first byte; -F keeps retrying a file that does not exist yet,
# which stdout.log will not until the runner's first write. -s 0.1 matters: tail's
# default re-check is one second, which alone put delivery over the latency budget.
TAIL = "tail -n +1 -F -s 0.1"


class LogTap:
    def __init__(self, adapter, sandbox_id: str, coalescer: LogCoalescer,
                 attempt_id: str, work: str = WORK):
        self.adapter = adapter
        self.sandbox_id = sandbox_id
        self.coalescer = coalescer
        self.attempt_id = attempt_id
        self.work = work
        self.handles = []
        self.threads = []
        self.degraded: list[str] = []

    def start(self, sources=(("stdout.log", "stdout"), (PROGRESS_FILE, PROGRESS_STREAM))):
        for filename, stream in sources:
            try:
                handle = self.adapter.exec_async(
                    self.sandbox_id, f"{TAIL} {self.work}/{filename}")
            except Exception as e:
                self.degraded.append(f"{stream}: {type(e).__name__}")
                continue
            self.handles.append(handle)
            thread = threading.Thread(target=self._follow, args=(handle, stream),
                                      name=f"logtap-{stream}", daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def _follow(self, handle, stream: str) -> None:
        def on_out(text):
            self.coalescer.feed(self.attempt_id, stream, text if isinstance(text, str)
                                else text.decode(errors="replace"))
        try:
            self.adapter.follow_logs(handle, on_out, on_out)
        except Exception as e:  # a broken tap is a degraded feed, never a failed run
            self.degraded.append(f"{stream}: {type(e).__name__}")

    def close(self, timeout: float = 5.0) -> None:
        """Order matters: kill the followers first, then flush. This runs before the
        sandbox is stopped, because auto_delete removes it out from under them."""
        for handle in self.handles:
            try:
                self.adapter.cancel_async(handle)
            except Exception:
                pass
        for thread in self.threads:
            thread.join(timeout=timeout / max(1, len(self.threads)))
        self.coalescer.close()


def start_log_tap(adapter, sandbox_id: str, bus, run_id: str, attempt_id: str,
                  total_seeds: int, held_out: bool = False, work: str = WORK):
    """Arm the progress side channel and start following. Returns None if anything at
    all goes wrong — the experiment carries on regardless."""
    try:
        adapter.write_file(sandbox_id, f"{work}/{MARKER_FILE}",
                           (PROGRESS_FILE + "\n").encode())
        coalescer = LogCoalescer(bus, run_id)
        coalescer.track(attempt_id, total_seeds, held_out=held_out)
        return LogTap(adapter, sandbox_id, coalescer, attempt_id, work).start()
    except Exception:
        return None

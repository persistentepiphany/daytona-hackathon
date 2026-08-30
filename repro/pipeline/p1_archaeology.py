"""P1 environment archaeology: build the environment statefully, ship it as S0.

Every action is appended to RECIPE.sh inside the sandbox, so the frozen snapshot
ships as both a binary artifact and a human-readable recipe. The smoke gate must
pass before freeze. Freeze records snapshot name + recipe hash + git sha in the
ledger; the run is thereafter immutable (ledger enforces single freeze).
"""

import hashlib
import shlex

from ..orchestrator.adapter import SandboxAdapter
from ..orchestrator.ledger import Ledger
from ..orchestrator.lifecycle import Lifecycle

WORK = "/home/daytona/work"


class ArchaeologyError(RuntimeError):
    pass


class ArchaeologySession:
    def __init__(self, lifecycle: Lifecycle, adapter: SandboxAdapter, ledger: Ledger,
                 run_id: str, base_snapshot: str, volumes: list[tuple[str, str]] | None = None,
                 ttl_minutes: int | None = None):
        self.adapter = adapter
        self.ledger = ledger
        self.run_id = run_id
        self.lifecycle = lifecycle
        self.recipe: list[str] = ["#!/bin/bash", "# environment recipe, replayed top to bottom", "set -e"]
        # queue on a quota refusal rather than killing the run at P1: with several
        # pipelines in flight the archaeology boxes are what contend for slots
        self.sandbox_id = lifecycle.create_with_retry(
            "archaeology", name=f"arch-{run_id}", snapshot=base_snapshot,
            volumes=volumes, ttl_minutes=ttl_minutes,
        )
        r = adapter.exec(self.sandbox_id, f"mkdir -p {WORK}")
        if r.exit_code != 0:
            raise ArchaeologyError(f"cannot create work dir: {r.output}")
        self._flush_recipe()

    def _flush_recipe(self) -> None:
        self.adapter.write_file(self.sandbox_id, f"{WORK}/RECIPE.sh",
                                ("\n".join(self.recipe) + "\n").encode())

    def sh(self, cmd: str, timeout: int = 1800, record: bool = True, check: bool = True):
        r = self.adapter.exec(self.sandbox_id, cmd, cwd=WORK, timeout=timeout)
        if record:
            self.recipe.append(cmd)
            self._flush_recipe()
        self.ledger.log_event(self.run_id, "archaeology_cmd",
                              {"cmd": cmd, "exit": r.exit_code, "tail": r.output[-500:]})
        if check and r.exit_code != 0:
            raise ArchaeologyError(f"command failed ({r.exit_code}): {cmd}\n{r.output[-2000:]}")
        return r

    def put_file(self, relpath: str, content: str) -> None:
        self.adapter.write_file(self.sandbox_id, f"{WORK}/{relpath}", content.encode())
        self.recipe.append(f"cat > {shlex.quote(relpath)} <<'RECIPE_EOF'\n{content}\nRECIPE_EOF")
        self._flush_recipe()
        self.ledger.log_event(self.run_id, "archaeology_file",
                              {"path": relpath, "sha256": hashlib.sha256(content.encode()).hexdigest()})

    def smoke(self) -> None:
        r = self.sh("bash smoke.sh", record=False, check=False, timeout=900)
        self.ledger.log_event(self.run_id, "smoke_gate",
                              {"exit": r.exit_code, "tail": r.output[-1000:]})
        if r.exit_code != 0:
            raise ArchaeologyError(f"smoke gate failed:\n{r.output[-2000:]}")

    def freeze(self, snapshot_name: str) -> dict:
        recipe_text = "\n".join(self.recipe) + "\n"
        recipe_sha = hashlib.sha256(recipe_text.encode()).hexdigest()
        self.sh("git init -q 2>/dev/null; git add -A 2>/dev/null; "
                "git -c user.name=archaeology -c user.email=archaeology@localhost "
                "commit -qm baseline 2>/dev/null; true", record=False, check=False)
        r = self.sh("git rev-parse HEAD 2>/dev/null || echo none", record=False, check=False)
        git_sha = (r.output or "none").strip().splitlines()[-1]
        self.adapter.create_snapshot(self.sandbox_id, snapshot_name)
        self.ledger.set_run_freeze(self.run_id, snapshot_name, git_sha, recipe_sha)
        self.ledger.log_event(self.run_id, "s0_frozen", {
            "snapshot": snapshot_name, "git_sha": git_sha, "recipe_sha": recipe_sha,
        })
        return {"snapshot": snapshot_name, "git_sha": git_sha, "recipe_sha": recipe_sha}

    def verify_s0_boot(self, snapshot_name: str,
                       volumes: list[tuple[str, str]] | None = None) -> None:
        """Boot a fresh sandbox from S0 and re-run the smoke gate there."""
        sid = self.lifecycle.create("experiment", name=f"s0check-{self.run_id}",
                                    snapshot=snapshot_name, ttl_minutes=20, volumes=volumes)
        try:
            r = self.adapter.exec(sid, "bash smoke.sh", cwd=WORK, timeout=900)
            self.ledger.log_event(self.run_id, "s0_boot_verified",
                                  {"exit": r.exit_code, "tail": r.output[-500:]})
            if r.exit_code != 0:
                raise ArchaeologyError(f"S0 boot verification failed:\n{r.output[-2000:]}")
        finally:
            self.lifecycle.delete(sid)

    def teardown(self) -> None:
        self.lifecycle.delete(self.sandbox_id)


def run_with_recovery(session: ArchaeologySession, cmd: str, parallel=None,
                      max_attempts: int = 3):
    """Search-on-failure: retry a failing environment command; once the same error
    signature recurs, one Parallel search may resolve environment mechanics
    (versions, mirrors, build flags — never method semantics; the client's stage
    gate and per-session cap enforce the budget). With Parallel absent or
    disabled, this degrades to blind retry.
    """
    last_sig = None
    searched = False
    result = None
    for attempt in range(max_attempts):
        result = session.sh(cmd, check=False)
        if result.exit_code == 0:
            return result
        lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
        sig = lines[-1][:200] if lines else "unknown-error"
        session.ledger.log_event(session.run_id, "recovery_attempt", {
            "cmd": cmd, "attempt": attempt + 1, "signature": sig,
        })
        if sig == last_sig and not searched and parallel is not None:
            try:
                hits = parallel.search(
                    "archaeology",
                    f"resolve environment build/install error: {sig}",
                    [sig], max_results=5,
                )
                searched = True
                session.ledger.log_event(session.run_id, "recovery_search", {
                    "signature": sig, "n_results": len(hits),
                })
            except Exception:
                pass  # disabled or capped: fall back to blind retry
        last_sig = sig
    raise ArchaeologyError(f"command failed after {max_attempts} attempts: {cmd}\n"
                           f"{(result.output if result else '')[-1000:]}")

"""Narrow interface between the orchestrator and the sandbox provider.

The orchestrator only ever talks to this surface, so every pipeline stage is
testable against the in-memory fake in tests/, and the real Daytona implementation
stays in one file. Methods return plain values (ids, exit codes, bytes) — no
provider objects leak upward.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ExecResult:
    exit_code: int
    output: str


@dataclass
class AsyncCmd:
    """Handle for a command running in a sandbox session, so its output can be followed
    while it runs. Synchronous `exec` remains the executor's path; this exists only for
    the log tap."""
    sandbox_id: str
    session_id: str
    cmd_id: str


@dataclass
class CreateSpec:
    name: str
    labels: dict[str, str]
    snapshot: str | None = None
    image: str | None = None  # base image ref for declarative creation
    ttl_minutes: int | None = None
    auto_stop_interval: int | None = None
    auto_pause_interval: int | None = None
    auto_delete_interval: int | None = None
    volumes: list[tuple[str, str]] = field(default_factory=list)  # (volume_id, mount_path)
    network_block_all: bool = False
    env_vars: dict[str, str] = field(default_factory=dict)
    resources: dict[str, object] | None = None  # {"cpu":, "memory":, "gpu":, "gpu_type":}


class SandboxAdapter(Protocol):
    def create(self, spec: CreateSpec) -> str: ...

    def fork(self, sandbox_id: str, name: str) -> str: ...

    def exec(self, sandbox_id: str, cmd: str, cwd: str | None = None,
             env: dict[str, str] | None = None, timeout: int | None = None) -> ExecResult: ...

    def create_snapshot(self, sandbox_id: str, name: str) -> None: ...

    def stop(self, sandbox_id: str) -> None: ...

    def start(self, sandbox_id: str) -> None: ...

    def delete(self, sandbox_id: str) -> None: ...

    def list_ids_by_label(self, key: str, value: str) -> list[str]: ...

    def read_file(self, sandbox_id: str, path: str) -> bytes: ...

    def write_file(self, sandbox_id: str, path: str, data: bytes) -> None: ...

    def volume_ensure(self, name: str) -> str: ...

    def volume_delete(self, name: str) -> None: ...

    def snapshot_exists(self, name: str) -> bool: ...

    def list_snapshots(self) -> list[dict]: ...

    def list_sandboxes(self) -> list[dict]: ...

    def preview_url(self, sandbox_id: str, port: int) -> str: ...

    # --- async session commands: used by the live feed's log tap only ---
    def exec_async(self, sandbox_id: str, cmd: str, cwd: str | None = None,
                   env: dict[str, str] | None = None) -> AsyncCmd: ...

    def follow_logs(self, handle: AsyncCmd, on_stdout, on_stderr=None) -> None: ...

    def cancel_async(self, handle: AsyncCmd) -> None: ...

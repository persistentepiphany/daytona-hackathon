"""In-memory sandbox adapter for tests: records every call, simulates fork lineage."""

import itertools

from repro.orchestrator.adapter import CreateSpec, ExecResult


class FakeAdapter:
    """Records every call so tests can assert on the exact SDK surface exercised.

    Covers the full surface named in ARCHITECTURE.md: create, exec, sessions,
    fs upload/download, create_snapshot, fork, stop/start/pause/delete, ttl and
    interval setters, labels, network settings, preview links, volumes, and
    refresh_activity. exec_responses maps a command substring to either one
    ExecResult or a list consumed in order (for scripted failure scenarios).
    """

    def __init__(self):
        self._seq = itertools.count(1)
        self._cmd_seq = itertools.count(1)
        self.sandboxes: dict[str, dict] = {}  # id -> {spec/labels/state/parent}
        self.snapshots: set[str] = {"base"}
        self.volumes: dict[str, str] = {}
        self.files: dict[tuple[str, str], bytes] = {}
        self.exec_log: list[tuple[str, str]] = []
        self.exec_responses: dict[str, ExecResult | list] = {}
        self.deleted_order: list[str] = []
        self.calls: list[tuple[str, tuple]] = []
        self.sessions: dict[tuple[str, str], list[str]] = {}
        self.async_cmds: dict[str, str] = {}
        self.stream_script: dict[str, list[str]] = {}
        self.cancelled: list[str] = []

    def _rec(self, name: str, *args) -> None:
        self.calls.append((name, args))

    def create(self, spec: CreateSpec) -> str:
        self._rec("create", spec.name, spec.snapshot or spec.image)
        if spec.snapshot and spec.snapshot not in self.snapshots:
            raise RuntimeError(f"unknown snapshot {spec.snapshot}")
        sid = f"sbx-{next(self._seq)}"
        self.sandboxes[sid] = {"spec": spec, "labels": dict(spec.labels),
                              "state": "started", "parent": None}
        return sid

    def fork(self, sandbox_id: str, name: str) -> str:
        self._rec("fork", sandbox_id, name)
        return self._fork(sandbox_id, name)

    def _fork(self, sandbox_id: str, name: str) -> str:
        if self.sandboxes[sandbox_id]["state"] != "started":
            raise RuntimeError("cannot fork a stopped sandbox")
        sid = f"sbx-{next(self._seq)}"
        parent = self.sandboxes[sandbox_id]
        self.sandboxes[sid] = {"spec": parent["spec"], "labels": dict(parent["labels"]),
                              "state": "started", "parent": sandbox_id}
        for (owner, path), data in list(self.files.items()):
            if owner == sandbox_id:
                self.files[(sid, path)] = data
        return sid

    def exec(self, sandbox_id: str, cmd: str, cwd=None, env=None, timeout=None) -> ExecResult:
        self._rec("exec", sandbox_id, cmd)
        if self.sandboxes[sandbox_id]["state"] != "started":
            raise RuntimeError("sandbox not running")
        self.exec_log.append((sandbox_id, cmd))
        for key, resp in self.exec_responses.items():
            if key in cmd:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        return ExecResult(0, "")

    def create_session(self, sandbox_id: str, session_id: str) -> None:
        self._rec("create_session", sandbox_id, session_id)
        self.sessions[(sandbox_id, session_id)] = []

    def execute_session_command(self, sandbox_id: str, session_id: str, cmd: str) -> ExecResult:
        self._rec("execute_session_command", sandbox_id, session_id, cmd)
        self.sessions[(sandbox_id, session_id)].append(cmd)
        return self.exec(sandbox_id, cmd)

    # --- async session commands: the live feed's log tap -------------------
    # stream_script maps a tailed path fragment to the chunks its follower yields, so
    # coalescer and tap tests are deterministic and need no sandbox.
    def exec_async(self, sandbox_id: str, cmd: str, cwd=None, env=None):
        from repro.orchestrator.adapter import AsyncCmd

        self._rec("exec_async", sandbox_id, cmd)
        cmd_id = f"cmd-{next(self._cmd_seq)}"
        self.async_cmds[cmd_id] = cmd
        return AsyncCmd(sandbox_id=sandbox_id, session_id=f"sess-{cmd_id}", cmd_id=cmd_id)

    def follow_logs(self, handle, on_stdout, on_stderr=None) -> None:
        self._rec("follow_logs", handle.sandbox_id, handle.cmd_id)
        cmd = self.async_cmds.get(handle.cmd_id, "")
        for key, chunks in self.stream_script.items():
            if key in cmd:
                for chunk in chunks:
                    on_stdout(chunk)
                return

    def cancel_async(self, handle) -> None:
        self._rec("cancel_async", handle.sandbox_id, handle.cmd_id)
        self.cancelled.append(handle.cmd_id)

    def create_snapshot(self, sandbox_id: str, name: str) -> None:
        self._rec("create_snapshot", sandbox_id, name)
        self.snapshots.add(name)

    def stop(self, sandbox_id: str) -> None:
        self._rec("stop", sandbox_id)
        box = self.sandboxes[sandbox_id]
        box["state"] = "stopped"
        if box["spec"].auto_delete_interval == 0:
            self.delete(sandbox_id)

    def start(self, sandbox_id: str) -> None:
        self._rec("start", sandbox_id)
        self.sandboxes[sandbox_id]["state"] = "started"

    def pause(self, sandbox_id: str) -> None:
        self._rec("pause", sandbox_id)
        self.sandboxes[sandbox_id]["state"] = "paused"

    def set_ttl(self, sandbox_id: str, minutes: int) -> None:
        self._rec("set_ttl", sandbox_id, minutes)
        self.sandboxes[sandbox_id]["ttl"] = minutes

    def set_autostop_interval(self, sandbox_id: str, minutes: int) -> None:
        self._rec("set_autostop_interval", sandbox_id, minutes)

    def set_auto_delete_interval(self, sandbox_id: str, minutes: int) -> None:
        self._rec("set_auto_delete_interval", sandbox_id, minutes)

    def set_labels(self, sandbox_id: str, labels: dict) -> None:
        self._rec("set_labels", sandbox_id, dict(labels))
        self.sandboxes[sandbox_id]["labels"].update(labels)

    def update_network_settings(self, sandbox_id: str, **kw) -> None:
        self._rec("update_network_settings", sandbox_id, dict(kw))

    def refresh_activity(self, sandbox_id: str) -> None:
        self._rec("refresh_activity", sandbox_id)

    def delete(self, sandbox_id: str) -> None:
        self._rec("delete", sandbox_id)
        if sandbox_id not in self.sandboxes:
            raise RuntimeError("not found")
        children = [sid for sid, b in self.sandboxes.items() if b["parent"] == sandbox_id]
        if children:
            raise RuntimeError(f"sandbox {sandbox_id} has active fork children {children}")
        del self.sandboxes[sandbox_id]
        self.deleted_order.append(sandbox_id)

    def list_ids_by_label(self, key: str, value: str) -> list[str]:
        return [sid for sid, b in self.sandboxes.items() if b["labels"].get(key) == value]

    def read_file(self, sandbox_id: str, path: str) -> bytes:
        self._rec("read_file", sandbox_id, path)
        try:
            return self.files[(sandbox_id, path)]
        except KeyError:
            raise FileNotFoundError(f"{sandbox_id}:{path}") from None

    def write_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        self._rec("write_file", sandbox_id, path)
        self.files[(sandbox_id, path)] = data

    def volume_ensure(self, name: str) -> str:
        self._rec("volume_ensure", name)
        return self.volumes.setdefault(name, f"vol-{len(self.volumes) + 1}")

    def volume_delete(self, name: str) -> None:
        self._rec("volume_delete", name)
        self.volumes.pop(name, None)

    def snapshot_exists(self, name: str) -> bool:
        return name in self.snapshots

    def preview_url(self, sandbox_id: str, port: int) -> str:
        self._rec("get_preview_link", sandbox_id, port)
        return f"https://fake-preview/{sandbox_id}/{port}"

    def signed_preview_url(self, sandbox_id: str, port: int, expires_in_seconds: int = 86400) -> str:
        self._rec("create_signed_preview_url", sandbox_id, port, expires_in_seconds)
        return f"https://fake-signed/{sandbox_id}/{port}"

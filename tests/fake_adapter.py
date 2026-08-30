"""In-memory sandbox adapter for tests: records every call, simulates fork lineage."""

import itertools

from repro.orchestrator.adapter import CreateSpec, ExecResult


class FakeAdapter:
    def __init__(self):
        self._seq = itertools.count(1)
        self.sandboxes: dict[str, dict] = {}  # id -> {spec/labels/state/parent}
        self.snapshots: set[str] = {"base"}
        self.volumes: dict[str, str] = {}
        self.files: dict[tuple[str, str], bytes] = {}
        self.exec_log: list[tuple[str, str]] = []
        self.exec_responses: dict[str, ExecResult] = {}  # cmd substring -> result
        self.deleted_order: list[str] = []

    def create(self, spec: CreateSpec) -> str:
        if spec.snapshot and spec.snapshot not in self.snapshots:
            raise RuntimeError(f"unknown snapshot {spec.snapshot}")
        sid = f"sbx-{next(self._seq)}"
        self.sandboxes[sid] = {"spec": spec, "labels": dict(spec.labels),
                              "state": "started", "parent": None}
        return sid

    def fork(self, sandbox_id: str, name: str) -> str:
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
        if self.sandboxes[sandbox_id]["state"] != "started":
            raise RuntimeError("sandbox not running")
        self.exec_log.append((sandbox_id, cmd))
        for key, resp in self.exec_responses.items():
            if key in cmd:
                return resp
        return ExecResult(0, "")

    def create_snapshot(self, sandbox_id: str, name: str) -> None:
        self.snapshots.add(name)

    def stop(self, sandbox_id: str) -> None:
        box = self.sandboxes[sandbox_id]
        box["state"] = "stopped"
        if box["spec"].auto_delete_interval == 0:
            self.delete(sandbox_id)

    def start(self, sandbox_id: str) -> None:
        self.sandboxes[sandbox_id]["state"] = "started"

    def delete(self, sandbox_id: str) -> None:
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
        try:
            return self.files[(sandbox_id, path)]
        except KeyError:
            raise FileNotFoundError(f"{sandbox_id}:{path}") from None

    def write_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        self.files[(sandbox_id, path)] = data

    def volume_ensure(self, name: str) -> str:
        return self.volumes.setdefault(name, f"vol-{len(self.volumes) + 1}")

    def volume_delete(self, name: str) -> None:
        self.volumes.pop(name, None)

    def snapshot_exists(self, name: str) -> bool:
        return name in self.snapshots

    def preview_url(self, sandbox_id: str, port: int) -> str:
        return f"https://fake-preview/{sandbox_id}/{port}"

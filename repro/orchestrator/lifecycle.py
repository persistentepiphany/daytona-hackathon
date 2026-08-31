"""Sandbox lifecycle policy: creation gated on G1, everything labeled, kill switch.

Encodes the per-class lifecycle table: interval semantics follow the provider
(auto_stop 0 = disabled; auto_delete 0 = delete immediately on stop; None = leave
provider default). TTL is the budget backstop on every class. Fork children are
tracked so teardown always deletes children before their parent.
"""

from dataclasses import dataclass

from .adapter import CreateSpec, SandboxAdapter
from .budget import Budget
from .gates import Gates
from .ledger import Ledger


@dataclass(frozen=True)
class Policy:
    auto_stop: int | None
    auto_pause: int | None
    auto_delete: int | None
    default_ttl: int  # minutes


POLICIES: dict[str, Policy] = {
    # archaeology box stays up as fallback root until S0 is verified; auto-pause is
    # VM/Windows-only, so containers rely on auto_stop 0 (disabled) + the TTL backstop
    "archaeology": Policy(auto_stop=0, auto_pause=None, auto_delete=None, default_ttl=480),
    # experiments: orchestrator stops after evidence pull; stop triggers delete
    "experiment": Policy(auto_stop=0, auto_pause=None, auto_delete=0, default_ttl=120),
    "fork_child": Policy(auto_stop=0, auto_pause=None, auto_delete=0, default_ttl=120),
    "data_stager": Policy(auto_stop=15, auto_pause=None, auto_delete=0, default_ttl=120),
    "gpu": Policy(auto_stop=None, auto_pause=None, auto_delete=0, default_ttl=90),
    "build": Policy(auto_stop=30, auto_pause=None, auto_delete=None, default_ttl=720),
    # demo window: idle-stop after 3h so a forgotten preview stops holding org
    # quota, but never mid-demo; TTL 12h is the backstop
    "build_demo": Policy(auto_stop=180, auto_pause=None, auto_delete=None, default_ttl=720),
}


class LifecycleError(RuntimeError):
    pass


# the org quota (measured: 10 GiB total sandbox memory) is the binding limit on
# how many sandboxes exist at once; the provider phrases the refusal differently
# depending on which ceiling is hit, so match the whole family
QUOTA_MARKERS = ("quota", "limit", "exceed", "capacity", "insufficient",
                 "no available", "too many")


def is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(m in text for m in QUOTA_MARKERS)


class Lifecycle:
    def __init__(self, adapter: SandboxAdapter, ledger: Ledger, gates: Gates,
                 budget: Budget, run_id: str):
        self.adapter = adapter
        self.ledger = ledger
        self.gates = gates
        self.budget = budget
        self.run_id = run_id

    def create(self, kind: str, *, name: str, snapshot: str | None = None,
               image: str | None = None, exp_id: str | None = None,
               ttl_minutes: int | None = None, volumes: list[tuple[str, str]] | None = None,
               network_block_all: bool = False, env_vars: dict[str, str] | None = None,
               resources: dict[str, object] | None = None) -> str:
        if kind not in POLICIES:
            raise LifecycleError(f"unknown sandbox kind {kind}")
        self.gates.require(self.run_id, "G1")  # no sandbox spend before Gate 1
        if kind == "gpu":
            self.gates.require(self.run_id, "G2")
        policy = POLICIES[kind]
        ttl = ttl_minutes if ttl_minutes is not None else policy.default_ttl
        # check before the spend, charge after it is real: a create the provider
        # refuses on quota costs nothing, so retrying does not eat the ceiling
        self.budget.check("sandbox_minutes", ttl)
        labels = {"run": self.run_id, "kind": kind}
        if exp_id:
            labels["exp"] = exp_id
        spec = CreateSpec(
            name=name, labels=labels, snapshot=snapshot, image=image,
            ttl_minutes=ttl, auto_stop_interval=policy.auto_stop,
            auto_pause_interval=policy.auto_pause, auto_delete_interval=policy.auto_delete,
            volumes=volumes or [], network_block_all=network_block_all,
            env_vars=env_vars or {}, resources=resources,
        )
        sandbox_id = self.adapter.create(spec)
        self.budget.charge("sandbox_minutes", ttl, note=f"{kind}:{name}")
        self.ledger.log_event(self.run_id, "sandbox_created", {
            "sandbox_id": sandbox_id, "kind": kind, "name": name, "exp_id": exp_id,
            "snapshot": snapshot, "parent_id": None, "ttl_minutes": ttl,
            "network_block_all": network_block_all,
        })
        return sandbox_id

    def create_with_retry(self, kind: str, *, attempts: int = 20, wait_seconds: int = 60,
                          on_wait=None, **kwargs) -> str:
        """create(), but a quota refusal waits for a slot instead of failing the run.

        Concurrent pipelines contend for the same org quota; whoever loses the
        race queues rather than dying, and anything that is not a quota refusal
        still raises immediately.
        """
        import time

        for attempt in range(attempts):
            try:
                return self.create(kind, **kwargs)
            except Exception as e:
                last = attempt == attempts - 1
                self.ledger.log_event(self.run_id, "sandbox_create_retry", {
                    "kind": kind, "name": kwargs.get("name"), "exp_id": kwargs.get("exp_id"),
                    "attempt": attempt + 1, "quota": is_quota_error(e), "error": str(e)[:300],
                })
                if last or not is_quota_error(e):
                    raise
                if on_wait:
                    on_wait(attempt + 1, e)
                time.sleep(wait_seconds)
        raise LifecycleError("unreachable")  # pragma: no cover

    def fork(self, parent_id: str, name: str, exp_id: str | None = None) -> str:
        self.gates.require(self.run_id, "G1")
        policy = POLICIES["fork_child"]
        self.budget.check("sandbox_minutes", policy.default_ttl)
        child_id = self.adapter.fork(parent_id, name)
        self.budget.charge("sandbox_minutes", policy.default_ttl, note=f"fork:{name}")
        self.ledger.log_event(self.run_id, "sandbox_created", {
            "sandbox_id": child_id, "kind": "fork_child", "name": name, "exp_id": exp_id,
            "snapshot": None, "parent_id": parent_id, "ttl_minutes": policy.default_ttl,
            "network_block_all": False,
        })
        return child_id

    def stop(self, sandbox_id: str) -> None:
        self.adapter.stop(sandbox_id)
        self.ledger.log_event(self.run_id, "sandbox_stopped", {"sandbox_id": sandbox_id})

    def delete(self, sandbox_id: str) -> None:
        self.adapter.delete(sandbox_id)
        self.ledger.log_event(self.run_id, "sandbox_deleted", {"sandbox_id": sandbox_id})

    def kill_all(self) -> list[str]:
        """Kill switch: delete every sandbox labeled with this run, children first."""
        import json as _json

        created = self.ledger.events_for(self.run_id, "sandbox_created")
        children = []
        parents = []
        for row in created:
            payload = _json.loads(row["payload"])
            (children if payload.get("parent_id") else parents).append(payload["sandbox_id"])
        deleted = []
        for sid in children + parents:
            try:
                self.adapter.delete(sid)
                deleted.append(sid)
            except Exception:
                pass  # already gone or never started; the label sweep below catches strays
        for sid in self.adapter.list_ids_by_label("run", self.run_id):
            try:
                self.adapter.delete(sid)
                deleted.append(sid)
            except Exception:
                pass
        self.ledger.log_event(self.run_id, "kill_switch", {"deleted": deleted})
        return deleted

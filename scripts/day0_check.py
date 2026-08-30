"""Day-0 empirical verification of the Daytona account against the design assumptions.

Runs the pre-build checklist live: snapshot API naming, VM-class availability, fork
support, freeze-and-boot (the S0 linchpin), concurrency headroom, network_block_all
behavior, volume mount semantics and propagation latency, resize ceilings,
GPU-from-image creation, org metadata, and one Parallel round-trip. Everything it
creates carries the label run=<day0-...> and is deleted on exit, children before
parents.

Usage: python scripts/day0_check.py [--skip-gpu] [--skip-parallel]
"""

import argparse
import json
import os
import sys
import time
import traceback

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from repro.orchestrator.daytona_client import enable_proxy_env  # noqa: E402

enable_proxy_env()

from daytona import (  # noqa: E402
    CreateSandboxFromImageParams,
    CreateSandboxFromSnapshotParams,
    Daytona,
    DaytonaConfig,
    GpuType,
    Image,
    ListSandboxesQuery,
    Resources,
    Sandbox,
    VolumeMount,
)

CONTAINER_SNAPSHOT = "daytona-small"
VM_SNAPSHOT = "daytona-vm-small"
API_URL = "https://app.daytona.io/api"


def env_key(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


class Day0:
    def __init__(self, skip_gpu: bool, skip_parallel: bool):
        self.skip_gpu = skip_gpu
        self.skip_parallel = skip_parallel
        self.label = f"day0-{int(time.time())}"
        self.results: dict[str, str] = {}
        self.sandboxes: list[Sandbox] = []  # creation order; teardown reversed
        self.probe_snapshot: str | None = None
        self.volume = None
        self.a: Sandbox | None = None
        self.d: Sandbox | None = None
        key = env_key("DAYTONA_API_KEY", "DAYTONA_API")
        if not key:
            raise SystemExit("no DAYTONA_API_KEY / DAYTONA_API in environment")
        self.key = key
        self.daytona = Daytona(DaytonaConfig(api_key=key))

    def record(self, item: str, value: str) -> None:
        self.results[item] = value
        print(f"  -> {item}: {value}", flush=True)

    def step(self, name, fn) -> None:
        print(f"[{name}]", flush=True)
        try:
            fn()
        except Exception as e:  # record and continue; one failed probe must not kill the rest
            self.record(name, f"FAIL: {type(e).__name__}: {str(e)[:200]}")
            traceback.print_exc()

    def create_box(self, name: str, **kw) -> Sandbox:
        t0 = time.monotonic()
        sbx = self.daytona.create(
            CreateSandboxFromSnapshotParams(
                snapshot=kw.pop("snapshot", CONTAINER_SNAPSHOT),
                name=f"{self.label}-{name}",
                labels={"run": self.label},
                ttl_minutes=kw.pop("ttl_minutes", 60),
                **kw,
            ),
            timeout=300,
        )
        dt = time.monotonic() - t0
        self.sandboxes.append(sbx)
        print(f"  created {name} id={sbx.id} in {dt:.1f}s", flush=True)
        sbx._day0_latency = dt
        return sbx

    # 1 -------------------------------------------------------------------
    def check_snapshot_api(self):
        pub = callable(getattr(Sandbox, "create_snapshot", None))
        exp = callable(getattr(Sandbox, "_experimental_create_snapshot", None))
        self.record("01 snapshot API", f"create_snapshot public={pub}, experimental alias={exp}")

    # VM class availability ------------------------------------------------
    def check_vm_class(self):
        errs = []
        for target in ("us", "eu"):
            try:
                d = Daytona(DaytonaConfig(api_key=self.key, target=target))
                s = d.create(
                    CreateSandboxFromSnapshotParams(
                        snapshot=VM_SNAPSHOT, name=f"{self.label}-vm-{target}",
                        labels={"run": self.label}, ttl_minutes=15,
                    ),
                    timeout=300,
                )
                self.sandboxes.append(s)
                self.record("02 VM class", f"AVAILABLE in {target}")
                return
            except Exception as e:
                errs.append(f"{target}: {str(e)[:80]}")
        self.record("02 VM class", "NOT AVAILABLE (" + " | ".join(errs) + ")")

    # volume ---------------------------------------------------------------
    def check_volume_create(self):
        self.volume = self.daytona.volume.get(f"{self.label}-vol", create=True)
        t0 = time.monotonic()
        while getattr(self.volume, "state", None) != "ready":
            if time.monotonic() - t0 > 120:
                raise TimeoutError(f"volume stuck in state {getattr(self.volume, 'state', '?')}")
            time.sleep(2)
            self.volume = self.daytona.volume.get(f"{self.label}-vol")
        self.record("06a volume create", f"ok, ready in {time.monotonic()-t0:.1f}s (async: starts pending_create)")
        ro = "read_only" in getattr(VolumeMount, "model_fields", {})
        self.record("06b VolumeMount read-only field", str(ro) + ("" if ro else " (enforce via checksums)"))

    # base sandbox ---------------------------------------------------------
    def check_base_create(self):
        mounts = [VolumeMount(volume_id=self.volume.id, mount_path="/vol")] if self.volume else None
        self.a = self.create_box("a", volumes=mounts)
        self.record("03a create-from-snapshot latency (container)", f"{self.a._day0_latency:.1f}s")
        r = self.a.process.exec("echo day0-marker > $HOME/marker.txt && cat $HOME/marker.txt")
        assert "day0-marker" in (r.result or ""), r.result
        self.record("03b exec inside sandbox", "ok")

    # fork -----------------------------------------------------------------
    def check_fork(self):
        if self.a is None:
            self.record("04 fork", "SKIPPED (no base sandbox)")
            return
        try:
            t0 = time.monotonic()
            child = self.a.fork(name=f"{self.label}-fork", timeout=300)
            dt = time.monotonic() - t0
            self.sandboxes.append(child)
            r = child.process.exec("cat $HOME/marker.txt")
            inherited = "day0-marker" in (r.result or "")
            self.record("04 fork", f"ok in {dt:.1f}s, state inherited={inherited}")
            self.daytona.delete(child, wait=True)
            self.sandboxes.remove(child)
        except Exception as e:
            self.record("04 fork", f"NOT AVAILABLE on this class: {str(e)[:160]}")

    # snapshot freeze + boot ----------------------------------------------
    def check_freeze_and_boot(self):
        if self.a is None:
            self.record("05 freeze-and-boot", "SKIPPED (no base sandbox)")
            return
        name = f"{self.label}-s0"
        t0 = time.monotonic()
        self.a.create_snapshot(name, timeout=900)
        dt = time.monotonic() - t0
        self.probe_snapshot = name
        self.record("05a create_snapshot live (freeze)", f"ok in {dt:.1f}s")
        c = self.create_box("froms0", snapshot=name)
        r = c.process.exec("cat $HOME/marker.txt")
        self.record("05b boot-from-frozen-snapshot", f"{c._day0_latency:.1f}s, state preserved={'day0-marker' in (r.result or '')}")
        self.daytona.delete(c, wait=True)
        self.sandboxes.remove(c)

    # network_block_all + volume propagation -------------------------------
    def check_blocked_sandbox(self):
        mounts = [VolumeMount(volume_id=self.volume.id, mount_path="/vol")] if self.volume else None
        self.d = self.create_box("blocked", network_block_all=True, volumes=mounts)
        probes = {
            "pypi.org": "https://pypi.org/simple/",
            "example.com": "https://example.com/",
            "files.pythonhosted.org": "https://files.pythonhosted.org/",
        }
        out = []
        for label, url in probes.items():
            r = self.d.process.exec(
                f"curl -sS -o /dev/null -m 8 -w '%{{http_code}}' {url} || echo ' curl-failed'"
            )
            txt = (r.result or "").strip()
            code = txt[:3]
            reachable = code.isdigit() and code != "000"
            out.append(f"{label}={'REACHABLE ' + code if reachable else 'BLOCKED'}")
        self.record("07 network_block_all", "; ".join(out))

    def check_volume_propagation(self):
        if self.a is None or self.d is None:
            self.record("06c volume propagation", "SKIPPED")
            return
        payload = json.dumps({"t": time.time()})
        self.a.process.exec(f"mkdir -p /vol && printf '{payload}' > /vol/probe.json && sync")
        t0 = time.monotonic()
        seen = None
        for _ in range(30):
            r = self.d.process.exec("cat /vol/probe.json 2>/dev/null || true")
            if r.result and '"t"' in r.result:
                seen = time.monotonic() - t0
                break
            time.sleep(2)
        self.record("06c volume propagation (small JSON)", f"{seen:.1f}s" if seen is not None else "NOT VISIBLE in 60s")

    # resize ---------------------------------------------------------------
    def check_resize(self):
        if self.a is None:
            self.record("08 resize", "SKIPPED (no base sandbox)")
            return
        try:
            self.a.resize(Resources(cpu=2, memory=2), timeout=120)
            self.a.wait_for_resize_complete(timeout=180)
            self.record("08 resize to 2cpu/2GiB", "ok")
        except Exception as e:
            self.record("08 resize to 2cpu/2GiB", f"REJECTED: {str(e)[:160]}")

    # concurrency ----------------------------------------------------------
    def check_concurrency(self):
        extras = []
        limit_hit = None
        base_alive = len([s for s in self.sandboxes])
        for i in range(3):
            try:
                extras.append(self.create_box(f"cc{i}", ttl_minutes=20))
            except Exception as e:
                limit_hit = f"at {base_alive + len(extras) + 1} concurrent: {str(e)[:120]}"
                break
        alive = base_alive + len(extras)
        self.record("09 concurrency probe", limit_hit or f">= {alive} concurrent sandboxes ok")
        for s in extras:
            self.daytona.delete(s, wait=True)
            self.sandboxes.remove(s)

    # GPU ------------------------------------------------------------------
    def check_gpu(self):
        if self.skip_gpu:
            self.record("10 GPU from declarative image", "SKIPPED (--skip-gpu)")
            return
        try:
            t0 = time.monotonic()
            g = self.daytona.create(
                CreateSandboxFromImageParams(
                    image=Image.base("python:3.11-slim"),
                    resources=Resources(gpu=1, gpu_type=GpuType.RTX_5090),
                    name=f"{self.label}-gpu",
                    labels={"run": self.label},
                    ttl_minutes=5,
                    auto_delete_interval=0,
                ),
                timeout=420,
                on_snapshot_create_logs=lambda line: print(f"    gpu-image: {line}", flush=True),
            )
            self.sandboxes.append(g)
            dt = time.monotonic() - t0
            r = g.process.exec("nvidia-smi -L || echo no-nvidia-smi")
            self.record("10 GPU from declarative image", f"ok in {dt:.0f}s; {(r.result or '').strip()[:120]}")
            self.daytona.delete(g, wait=True)
            self.sandboxes.remove(g)
        except Exception as e:
            self.record("10 GPU from declarative image", f"NOT AVAILABLE: {type(e).__name__}: {str(e)[:200]}")

    # org / tier -----------------------------------------------------------
    def check_org(self):
        with httpx.Client(headers={"Authorization": f"Bearer {self.key}"}, timeout=20) as c:
            r = c.get(API_URL + "/organizations")
            self.record("11 org endpoint with API key", f"{r.status_code} (org endpoints need user auth, not API key)")
        self.record("12 credit stacking", "MANUAL: dashboard-only, not exposed via API")

    # Parallel -------------------------------------------------------------
    def check_parallel(self):
        if self.skip_parallel:
            self.record("13 Parallel round-trip", "SKIPPED (--skip-parallel)")
            return
        pkey = env_key("PARALLEL_API_KEY", "PARALLEL_API")
        if not pkey:
            self.record("13 Parallel round-trip", "no key in environment")
            return
        body = {
            "objective": "Locate any official or third-party source code release for the paper 'Random Forests' (Breiman, 2001).",
            "search_queries": ["Breiman Random Forests 2001 official source code release"],
            "processor": "base",
            "max_results": 3,
        }
        r = httpx.post(
            "https://api.parallel.ai/v1beta/search",
            headers={"x-api-key": pkey, "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        n = len(r.json().get("results", [])) if r.status_code == 200 else None
        self.record("13 Parallel round-trip", f"HTTP {r.status_code}, results={n}" if n is not None else f"HTTP {r.status_code}: {r.text[:200]}")

    # teardown -------------------------------------------------------------
    def teardown(self):
        print("[teardown]", flush=True)
        for sbx in reversed(self.sandboxes):
            try:
                self.daytona.delete(sbx, wait=True)
                print(f"  deleted {sbx.id}", flush=True)
            except Exception as e:
                print(f"  delete {sbx.id} failed: {e}", flush=True)
        try:
            leftovers = list(self.daytona.list(ListSandboxesQuery(labels={"run": self.label})))
            for s in leftovers:
                try:
                    self.daytona.delete(s, wait=True)
                    print(f"  swept leftover {s.id}", flush=True)
                except Exception as e:
                    print(f"  sweep of {s.id}: {str(e)[:80]}", flush=True)
            print(f"  label sweep found {len(leftovers)} leftover(s)", flush=True)
        except Exception as e:
            print(f"  label sweep failed: {e}", flush=True)
        if self.probe_snapshot:
            try:
                snap = self.daytona.snapshot.get(self.probe_snapshot)
                self.daytona.snapshot.delete(snap)
                print(f"  deleted snapshot {self.probe_snapshot}", flush=True)
            except Exception as e:
                print(f"  snapshot delete failed: {e}", flush=True)
        if self.volume is not None:
            try:
                self.daytona.volume.delete(self.volume)
                print("  deleted volume", flush=True)
            except Exception as e:
                print(f"  volume delete failed: {e}", flush=True)

    def run(self) -> None:
        try:
            self.step("snapshot-api", self.check_snapshot_api)
            self.step("vm-class", self.check_vm_class)
            self.step("volume-create", self.check_volume_create)
            self.step("base-create", self.check_base_create)
            self.step("fork", self.check_fork)
            self.step("freeze-and-boot", self.check_freeze_and_boot)
            self.step("blocked-sandbox", self.check_blocked_sandbox)
            self.step("volume-propagation", self.check_volume_propagation)
            self.step("resize", self.check_resize)
            self.step("concurrency", self.check_concurrency)
            self.step("gpu", self.check_gpu)
            self.step("org", self.check_org)
            self.step("parallel", self.check_parallel)
        finally:
            self.teardown()
        print("\n## Day-0 results\n")
        print("| Item | Result |")
        print("|---|---|")
        for k in sorted(self.results):
            print(f"| {k} | {self.results[k]} |")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skip-gpu", action="store_true")
    p.add_argument("--skip-parallel", action="store_true")
    args = p.parse_args()
    Day0(skip_gpu=args.skip_gpu, skip_parallel=args.skip_parallel).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

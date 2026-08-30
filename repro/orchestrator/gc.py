"""Quota garbage collection: the org quota is the scarcest resource in this system.

Two things accumulate and are never reclaimed by the provider or by a run's own
teardown: the S0 snapshot each run freezes (`s0-<run_id>`, ~14.5 GB of registry
storage, declaring the base's disk footprint), and the P5 preview sandbox, which
is deliberately excluded from `scripts/kill_stray.py` so a demo survives. Both
hold quota against every later run, which is how a 10 GiB ceiling gets spent by
runs that finished hours ago.

Nothing here deletes a snapshot belonging to a run that never finished, and the
newest preview is kept by default so the live demo URL keeps resolving.
"""

import re
import time
from datetime import datetime, timezone

S0_PREFIX = "s0-"
DEMO_KINDS = ("build", "build_demo")


def _age_hours(created_at: str | None) -> float | None:
    if not created_at:
        return None
    text = str(created_at).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (time.time() - dt.timestamp()) / 3600.0


def run_id_of_snapshot(name: str) -> str | None:
    """`s0-auto-1788102379-ab12cd` -> `auto-1788102379-ab12cd`; None if not ours."""
    if not name or not name.startswith(S0_PREFIX):
        return None
    return name[len(S0_PREFIX):] or None


def plan_snapshots(snapshots: list[dict], *, keep_runs: set[str], keep_newest: int = 1,
                   min_age_hours: float = 0.0) -> tuple[list[dict], list[dict]]:
    """Split our S0 snapshots into (delete, keep). Provider base images are never ours."""
    ours = []
    for s in snapshots:
        rid = run_id_of_snapshot(s.get("name") or "")
        if rid is None:
            continue
        ours.append({**s, "run_id": rid, "age_hours": _age_hours(s.get("created_at"))})
    # newest first; created_at is ISO so a lexical sort is chronological, and the
    # unix stamp inside our own run ids breaks ties when the provider omits it
    ours.sort(key=lambda s: (str(s.get("created_at") or ""), _stamp(s["run_id"])), reverse=True)
    delete, keep = [], []
    for i, s in enumerate(ours):
        age = s["age_hours"]
        if s["run_id"] in keep_runs:
            s["reason"] = "run pinned"
        elif i < keep_newest:
            s["reason"] = "newest"
        elif age is not None and age < min_age_hours:
            s["reason"] = f"younger than {min_age_hours}h"
        else:
            delete.append(s)
            continue
        keep.append(s)
    return delete, keep


def plan_sandboxes(sandboxes: list[dict], *, keep_runs: set[str], keep_newest_demo: int = 1,
                   min_age_hours: float = 0.0) -> tuple[list[dict], list[dict]]:
    """Split idle preview sandboxes into (delete, keep); non-demo boxes are left
    alone here - a run in flight owns those, and kill_stray.py covers crashes."""
    demos = [{**b, "age_hours": _age_hours(b.get("created_at"))}
             for b in sandboxes if (b.get("labels") or {}).get("kind") in DEMO_KINDS]
    demos.sort(key=lambda b: (str(b.get("created_at") or ""),
                              _stamp((b.get("labels") or {}).get("run", ""))), reverse=True)
    delete, keep = [], []
    for i, b in enumerate(demos):
        run = (b.get("labels") or {}).get("run", "")
        age = b["age_hours"]
        if run in keep_runs:
            b["reason"] = "run pinned"
        elif i < keep_newest_demo:
            b["reason"] = "newest preview (demo URL)"
        elif age is not None and age < min_age_hours:
            b["reason"] = f"younger than {min_age_hours}h"
        else:
            delete.append(b)
            continue
        keep.append(b)
    return delete, keep


def _stamp(run_id: str) -> int:
    m = re.search(r"(\d{9,})", run_id or "")
    return int(m.group(1)) if m else 0


def reclaimed(snapshots: list[dict], sandboxes: list[dict]) -> dict:
    """What the plan gives back, in the units the quota is denominated in."""
    return {
        "snapshots": len(snapshots),
        "snapshot_storage_gb": round(sum(float(s.get("size_gb") or 0) for s in snapshots), 1),
        "sandboxes": len(sandboxes),
        "memory_gib": sum(int(b.get("memory_gib") or 0) for b in sandboxes),
        "disk_gib": sum(int(b.get("disk_gib") or 0) for b in sandboxes),
    }

"""Completion estimates that are measured or enforced, never predicted.

The rule this module exists to keep: no point estimate for anything stochastic. A
training run's duration is not predictable from a paper, so nothing here claims to
predict one. What it does instead:

* per-attempt progress is an extrapolation of that attempt's *own* observed seed rate,
  reported only after at least one seed has finished;
* fleet timing is a queue simulation over the pool width using a measured median, and
  is reported as a band (p25/median/p75), never as a single number;
* the run ceiling is not an estimate at all. It is the sum of remaining TTLs, bounded by
  the budget's remaining sandbox minutes. `Lifecycle.create` pre-charges the whole TTL
  and `Budget.charge` raises before spending, so the run provably cannot exceed it.

While an implementer round is pending there is no honest whole-run number, because the
number of further rounds is not knowable; `run_completion` returns None and the UI shows
the ceiling alone.
"""

import heapq
import statistics

CONFIG_DEFAULT_SECONDS = 900.0


def attempt_eta(done: int, total: int, elapsed_s: float) -> float | None:
    """elapsed/k x (n-k) — this attempt's measured seed rate, nothing else. None until
    a seed has actually completed, because before that there is no rate to measure."""
    if not total or done <= 0 or done > total or elapsed_s <= 0:
        return None
    return round(elapsed_s / done * (total - done), 1)


def duration_prior(current_run_durations, history_durations=None,
                   default_s: float = CONFIG_DEFAULT_SECONDS) -> dict:
    """Median attempt duration with its provenance named.

    Priors in order: this run's own completed attempts, then ledger history for the same
    paper, then the configured default. The band collapses on the default because one
    number from a config file is not a measurement and must not be dressed up as one.
    """
    for samples, basis in ((current_run_durations, "current_run"),
                           (history_durations or [], "ledger_history")):
        values = sorted(float(v) for v in samples if v and v > 0)
        if len(values) >= 3:
            return {
                "basis": basis,
                "n_samples": len(values),
                "median_s": round(statistics.median(values), 1),
                "low_s": round(_quantile(values, 0.25), 1),
                "high_s": round(_quantile(values, 0.75), 1),
            }
    return {"basis": "config_default", "n_samples": 0, "median_s": round(default_s, 1),
            "low_s": None, "high_s": None}


def _quantile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _elapsed(attempt: dict, now: float) -> float:
    started = attempt.get("started")
    if started is None:
        return 0.0
    return max(0.0, now - float(started))


def simulate_queue(now: float, running: list[dict], pending: int, width: int,
                   per_attempt_s: float) -> float:
    """List scheduling over `width` slots. Returns seconds until the last slot frees.

    A running attempt's remaining time is capped by its own TTL: the provider kills it
    at the TTL whatever the median says, so the simulation can never project past the
    bound the platform enforces.
    """
    width = max(1, int(width))
    free: list[float] = []
    for att in running:
        elapsed = _elapsed(att, now)
        remaining = max(0.0, per_attempt_s - elapsed)
        ttl_s = att.get("ttl_s")
        if ttl_s:
            remaining = min(remaining, max(0.0, float(ttl_s) - elapsed))
        free.append(remaining)
    while len(free) < width:
        free.append(0.0)
    heapq.heapify(free)
    for _ in range(max(0, int(pending))):
        heapq.heappush(free, heapq.heappop(free) + per_attempt_s)
    return round(max(free) if free else 0.0, 1)


def fleet_eta(now: float, running: list[dict], pending: int, width: int,
              prior: dict) -> dict:
    """A band, never a point. On the config-default prior the band is withheld entirely
    and the caller is told to show the ceiling alone."""
    mid = simulate_queue(now, running, pending, width, float(prior["median_s"]))
    out = {
        "basis": prior["basis"], "n_samples": prior["n_samples"],
        "median_s": prior["median_s"], "mid_s": mid,
        "running": len(running), "pending": int(pending), "width": int(width),
    }
    if prior.get("low_s") and prior.get("high_s"):
        out["low_s"] = simulate_queue(now, running, pending, width, float(prior["low_s"]))
        out["high_s"] = simulate_queue(now, running, pending, width, float(prior["high_s"]))
    else:
        out["low_s"] = out["high_s"] = None
        out["note"] = "no measurements yet - ceiling only"
    return out


def run_ceiling_s(open_attempts: list[dict], now: float,
                  budget_remaining_minutes: float | None = None,
                  rounds_remaining: int = 0, round_ceiling_s: float = 0.0) -> float:
    """Sum of remaining TTLs plus the remaining round cap, bounded by the budget.

    Deliberately loose: TTLs run concurrently, so the sum overshoots the wall clock.
    That is the point — it is an upper bound the run cannot cross, not a guess at when
    it will finish.
    """
    total = 0.0
    for att in open_attempts:
        ttl_s = float(att.get("ttl_s") or 0.0)
        total += max(0.0, ttl_s - _elapsed(att, now))
    total += max(0, int(rounds_remaining)) * float(round_ceiling_s)
    if budget_remaining_minutes is not None:
        # the budget is enforced before any spend happens, so it bounds the TTL sum
        total = min(total, max(0.0, float(budget_remaining_minutes)) * 60.0)
    return round(total, 1)


def run_completion(fleet: dict, round_pending: bool) -> dict | None:
    """The whole-run band, or None while an implementer round is outstanding.

    Returning None is the honest answer, not a missing feature: with a further round
    possible, any completion time would be a guess about work not yet proposed.
    """
    if round_pending or fleet.get("low_s") is None:
        return None
    return {"low_s": fleet["low_s"], "mid_s": fleet["mid_s"], "high_s": fleet["high_s"],
            "basis": fleet["basis"]}


def round_label(k: int, cap: int) -> str:
    """Rounds are counted and capped, never predicted."""
    return f"round {int(k)} of ≤{int(cap)}"

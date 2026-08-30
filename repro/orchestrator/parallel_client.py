"""Parallel Search client: capped, stage-gated, fully logged, never load-bearing.

Two sanctioned call sites: the code-absence certification at intake, and
search-on-failure during archaeology (environment mechanics only — never method
semantics). Every query and result set is logged to the ledger. The pipeline must
complete end-to-end with this disabled; callers treat ParallelDisabled as a normal
fall-through, not an error.
"""

import httpx

from ..env import env_key

from .budget import Budget
from .ledger import Ledger

SEARCH_URL = "https://api.parallel.ai/v1beta/search"
DEFAULT_STAGES = ("intake", "archaeology")


class ParallelDisabled(RuntimeError):
    pass


class ParallelClient:
    def __init__(self, ledger: Ledger, run_id: str, budget: Budget,
                 enabled_stages: tuple[str, ...] = DEFAULT_STAGES,
                 per_stage_caps: dict[str, int] | None = None,
                 api_key: str | None = None):
        self.ledger = ledger
        self.run_id = run_id
        self.budget = budget
        self.enabled_stages = tuple(enabled_stages)
        self.per_stage_caps = dict(per_stage_caps or {"intake": 3, "archaeology": 10})
        self.api_key = api_key or env_key("PARALLEL_API_KEY", "PARALLEL_API")
        self._stage_counts: dict[str, int] = {}

    def enabled(self, stage: str) -> bool:
        return bool(self.api_key) and stage in self.enabled_stages

    def search(self, stage: str, objective: str, queries: list[str],
               max_results: int = 5) -> list[dict]:
        if not self.enabled(stage):
            raise ParallelDisabled(f"parallel disabled for stage {stage}")
        used = self._stage_counts.get(stage, 0)
        if used >= self.per_stage_caps.get(stage, 0):
            raise ParallelDisabled(f"parallel cap reached for stage {stage} ({used})")
        self.budget.charge("parallel_calls", 1, note=f"{stage}: {objective[:80]}")
        self._stage_counts[stage] = used + 1
        r = httpx.post(
            SEARCH_URL,
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
            json={"objective": objective, "search_queries": queries,
                  "processor": "base", "max_results": max_results},
            timeout=90,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        self.ledger.log_event(self.run_id, "parallel_search", {
            "stage": stage, "objective": objective, "queries": queries,
            "n_results": len(results),
            "urls": [x.get("url") for x in results],
        })
        return results

    def code_absence_certification(self, title: str, authors: list[str]) -> dict:
        """The wedge criterion, certified rather than assumed. Returns the query
        trail; the human decides at G1 whether the criterion holds."""
        queries = [
            f"{title} official source code repository",
            f"{title} {authors[0]} code release github",
            f"{title} supplementary code zenodo OSF",
        ]
        objective = (f"Determine whether any official or third-party source code "
                     f"release exists for the paper '{title}'.")
        try:
            results = self.search("intake", objective, queries, max_results=8)
            status = "COMPLETED"
        except ParallelDisabled as e:
            results, status = [], f"SKIPPED: {e}"
        return {
            "title": title,
            "objective": objective,
            "queries": queries,
            "results": [{"url": x.get("url"), "title": x.get("title")} for x in results],
            "status": status,
        }

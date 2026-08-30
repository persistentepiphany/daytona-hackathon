from __future__ import annotations

import os

import httpx

from repro.pipeline.p0_intake import evaluate_code_existence


SEARCH_URL = "https://api.parallel.ai/v1beta/search"


def certify_code_availability(title: str, authors: list[str], client: httpx.Client | None = None) -> dict:
    key = os.environ.get("PARALLEL_API_KEY") or os.environ.get("PARALLEL_API")
    queries = [f"{title} official source code repository", f"{title} github code"]
    if authors:
        queries.append(f"{title} {authors[0]} software implementation")
    if not key:
        outcome, certificate = evaluate_code_existence([])
        return {**certificate, "status": "SKIPPED: Parallel key unavailable", "queries": queries,
                "outcome": outcome}
    owned = client is None
    client = client or httpx.Client(timeout=90, follow_redirects=True)
    try:
        response = client.post(SEARCH_URL, headers={"x-api-key": key}, json={
            "objective": f"Find source-code releases associated with '{title}'.",
            "search_queries": queries, "processor": "base", "max_results": 8,
        })
        response.raise_for_status()
        raw = response.json().get("results", [])
        # Only URL/title metadata crosses the intake boundary.
        results = [{"url": item.get("url"), "title": item.get("title")}
                   for item in raw if item.get("url")]
        alive: dict[str, bool] = {}
        for item in results:
            try:
                check = client.head(item["url"], timeout=10)
                alive[item["url"]] = check.status_code < 400
            except httpx.HTTPError:
                alive[item["url"]] = False
        outcome, certificate = evaluate_code_existence(results, link_alive=alive)
        return {**certificate, "status": "COMPLETED", "queries": queries, "outcome": outcome}
    finally:
        if owned:
            client.close()

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from repro.env import env_key
from repro.pipeline.p0_intake import evaluate_code_existence


SEARCH_URL = "https://api.parallel.ai/v1beta/search"
CODE_HOSTS = {
    "github.com", "gitlab.com", "bitbucket.org", "zenodo.org", "osf.io",
    "codeocean.com", "sourceforge.net", "huggingface.co",
}
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a", "an", "and", "for", "from", "in", "of", "on", "the", "to", "using", "with",
    "method", "methods", "paper", "source", "code", "implementation", "official", "repository",
}
GENERIC_TECH_TOKENS = {
    "algorithm", "analysis", "benchmark", "classification", "data", "dataset", "deep",
    "difference", "equation", "equations", "experiment", "finite", "forest", "image",
    "learning", "machine", "model", "network", "neural", "newton", "nonlinear",
    "optimization", "random", "results", "simulation", "solving", "system", "training",
}


def _tokens(value: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(value.lower())
            if len(token) >= 3 and token not in STOPWORDS}


def code_release_candidates(results: list[dict], title: str, authors: list[str]) -> list[dict]:
    """Keep relevant releases, not papers or unrelated repositories.

    Parallel is broad by design. A result may reach the deterministic FOUND gate
    only when it is on an approved code/archive host and its metadata matches at
    least two distinctive title tokens or an author surname.
    """
    title_tokens = _tokens(title)
    distinctive_tokens = title_tokens - GENERIC_TECH_TOKENS
    surnames = {tokens[-1] for author in authors if (tokens := TOKEN_RE.findall(author.lower()))
                and len(tokens[-1]) >= 4}
    candidates: list[dict] = []
    for item in results:
        url = str(item.get("url") or "")
        hostname = (urlparse(url).hostname or "").lower()
        if not any(hostname == host or hostname.endswith(f".{host}") for host in CODE_HOSTS):
            continue
        haystack = f"{url} {item.get('title') or ''}".lower()
        overlap = title_tokens & _tokens(haystack)
        title_match = len(overlap) >= 2 and (not distinctive_tokens or bool(overlap & distinctive_tokens))
        author_match = any(surname in haystack for surname in surnames)
        if not title_match and not author_match:
            continue
        candidates.append({"url": url, "title": item.get("title")})
    return candidates


def certify_code_availability(title: str, authors: list[str], client: httpx.Client | None = None) -> dict:
    key = env_key("PARALLEL_API_KEY", "PARALLEL_API")
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
        search_results = [{"url": item.get("url"), "title": item.get("title")}
                          for item in raw if item.get("url")]
        results = code_release_candidates(search_results, title, authors)
        alive: dict[str, bool] = {}
        for item in results:
            try:
                check = client.head(item["url"], timeout=10)
                alive[item["url"]] = check.status_code < 400
            except httpx.HTTPError:
                alive[item["url"]] = False
        outcome, certificate = evaluate_code_existence(results, link_alive=alive)
        return {**certificate, "status": "COMPLETED", "queries": queries, "outcome": outcome,
                "search_results_reviewed": len(search_results),
                "promotion_rule": "approved code host plus title/author relevance"}
    finally:
        if owned:
            client.close()

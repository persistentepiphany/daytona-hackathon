"""P0 intake: paper-class gate and the three-outcome code-existence gate.

Paper classes: 1 reported_numbers, 2 provable_properties, 3 worked_examples,
4 nothing_checkable. The pipeline proceeds only for class 1; every other class
declines with the class named in the message — no further logic exists for them.

Code-existence outcomes: NOT_FOUND proceeds; REFERENCED_BUT_DEAD proceeds with the
dead link recorded on the certificate; FOUND declines, and the certificate is the
output. Certificates carry metadata only (urls, titles, timestamps) — found code
contents are never fetched, stored, or passed into any model context; the
sanitizer in this module is the enforcement point, not a convention.
"""

import time

from ..roles.base import LLMProvider, RoleError, extract_json

PAPER_CLASSES = {
    1: "reported_numbers",
    2: "provable_properties",
    3: "worked_examples",
    4: "nothing_checkable",
}

CODE_OUTCOMES = ("NOT_FOUND", "REFERENCED_BUT_DEAD", "FOUND")

CLASSIFIER_SYSTEM = """You classify a paper for an execution-based reproduction
pipeline. Return strictly one JSON object: {"paper_class": n, "rationale": "..."}
where n is: 1 if the paper reports concrete numbers obtainable by running code
(tables, metrics, simulation results); 2 if it only proves properties; 3 if it
only walks through worked examples; 4 if nothing in it is checkable by execution."""

# metadata the certificate may carry — anything else a search result contains
# (snippets, excerpts, file contents) is dropped here and never reaches a model
CERTIFICATE_KEYS = ("url", "title")


class IntakeDeclined(RuntimeError):
    pass


def classify_paper(provider: LLMProvider, paper_text: str) -> dict:
    result = extract_json(provider.complete(CLASSIFIER_SYSTEM, paper_text[:150000]))
    cls = result.get("paper_class")
    if cls not in PAPER_CLASSES:
        raise RoleError(f"classifier returned invalid paper_class {cls!r}")
    return {"paper_class": cls, "label": PAPER_CLASSES[cls],
            "rationale": result.get("rationale", "")}


def sanitize_result(result: dict) -> dict:
    out = {k: result.get(k) for k in CERTIFICATE_KEYS}
    out["seen_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return out


def evaluate_code_existence(results: list[dict],
                            link_alive: dict[str, bool] | None = None) -> tuple[str, dict]:
    """Decide the outcome from search-result metadata. link_alive maps url ->
    reachability, supplied by the caller (a fixture offline, a HEAD probe live);
    with no liveness information, any result counts as FOUND (the conservative
    reading). Returns (outcome, certificate)."""
    sanitized = [sanitize_result(r) for r in results]
    certificate = {"queries_returned": len(results), "results": sanitized}
    if not results:
        return "NOT_FOUND", dict(certificate, outcome="NOT_FOUND")
    if link_alive is not None:
        alive = [s for s in sanitized if link_alive.get(s["url"], False)]
        if not alive:
            return "REFERENCED_BUT_DEAD", dict(certificate, outcome="REFERENCED_BUT_DEAD",
                                               dead_links=[s["url"] for s in sanitized])
    else:
        alive = sanitized
    if alive:
        return "FOUND", dict(certificate, outcome="FOUND",
                             found=[s["url"] for s in alive])
    return "REFERENCED_BUT_DEAD", dict(certificate, outcome="REFERENCED_BUT_DEAD")


def intake_decision(paper_class: int, code_outcome: str) -> dict:
    """The single deterministic gate: proceed only for class-1 papers whose code
    search came back NOT_FOUND or REFERENCED_BUT_DEAD."""
    if paper_class != 1:
        return {"proceed": False,
                "reason": f"declined: paper_class {paper_class} "
                          f"({PAPER_CLASSES[paper_class]}) - the pipeline executes "
                          f"reported numbers only"}
    if code_outcome not in CODE_OUTCOMES:
        raise ValueError(f"unknown code outcome {code_outcome}")
    if code_outcome == "FOUND":
        return {"proceed": False,
                "reason": "declined: an implementation already exists; "
                          "the certificate is the output"}
    return {"proceed": True, "reason": f"code search: {code_outcome}"}

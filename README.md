# Preregistered Paper Reproduction on Daytona

A paper's claims become preregistered executable counterfactuals; each runs from the same frozen state with independent lineage on Daytona; the evidence — not the paper's authority — decides the verdict and what gets built.

## 1. How it works

1. **P0 Intake** — the Planner reads the paper PDF and emits a claims table, an ambiguity ledger, and a proposed experiment set drawn from a fixed menu (`reproduce, ablation, stronger_baseline, randomized_control, seed_sweep`), plus a code-absence certification via Parallel Search.
2. **G1 Approve & Freeze** — one user action. The orchestrator selects held-out claims, fixes tolerances, writes `prereg.json`, and commits its sha256 hash as commit #1 of the output repo. Nothing downstream may alter it.
3. **P1 Environment archaeology** — the Implementer iterates inside a Daytona VM, appending every action to `/work/RECIPE.sh`. A smoke gate (imports, data loader, one forward pass) must pass, then the VM is frozen as snapshot **S₀**.
4. **P2 Experiments** — one sandbox per scientific question, created directly from S₀, seeds looping inside the sandbox. Standing controls run in the same queue: a calibration paper (expected REPRODUCED), a sham twin with corrupted claims (expected NOT REPRODUCED), and a hermeticity run with networking blocked.
5. **P3 Verdict** — a sealed Verifier compares evidence to the preregistration only. Verdicts: `REPRODUCED WITHIN TOLERANCE / REPRODUCED OUTSIDE PREREGISTERED TOLERANCE / NOT REPRODUCED / UNDER-CONSTRAINED / NOT ATTEMPTABLE / INCONCLUSIVE`.
6. **P4 Adaptive round (optional)** — at most one, from the same menu, under a new prereg document requiring approval; it cannot alter primary verdicts.
7. **P5 Thin build + G3** — the Builder sees only the validated-knowledge brief and ships one API endpoint plus one static page from a container sandbox, exposed via a preview URL. G3 = user approves the push.

## 2. Invariants

1. **S₀ is immutable.** After freeze, nothing modifies the canonical snapshot; all work happens in sandboxes created from it.
2. **No sandbox spend before Gate 1.**
3. **Every experiment manifest references the prereg hash** and is rejected deterministically on any mismatch.
4. **The Verifier is sealed** — it sees prereg + evidence only.
5. **Every attempt is replayable from the ledger**: `S₀ + manifest + dataset hashes` reconstructs any run.
6. **The web exists only upstream of the freeze.** From S₀ onward the system is closed.

## 3. Repository layout

1. `repro/orchestrator/` — deterministic core: ledger, gates, prereg hashing, manifest validation, sandbox lifecycle, budget, evidence collection. No LLM calls.
2. `repro/roles/` — the four LLM roles (Planner, Implementer, Verifier, Builder). Agents propose; the orchestrator disposes.
3. `repro/pipeline/` — the P0–P5 stage drivers.
4. `repro/cli.py` — the `repro` command-line entry point.
5. `scripts/day0_check.py` — the Day-0 empirical verification checklist, runnable against a live Daytona account.
6. `schemas/` — JSON Schemas for claims, prereg, and experiment manifests.
7. `tests/` — unit tests against a fake Daytona adapter; no network required.

## 4. Setup

1. Python 3.11+.
2. `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`
3. Environment variables: `DAYTONA_API_KEY` (or `DAYTONA_API`), `PARALLEL_API_KEY` (or `PARALLEL_API`), `ANTHROPIC_API_KEY` for the LLM roles.
4. `.venv/bin/pytest` to run the unit tests.
5. `.venv/bin/python scripts/day0_check.py` to run the live Day-0 verification (creates and deletes small labeled sandboxes; minimal spend).

## 5. Status

1. Project scaffold in place.

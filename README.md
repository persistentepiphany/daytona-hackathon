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

## 5. Day-0 verification (live, against the event account)

Run: `python scripts/day0_check.py`. Findings so far:

1. `create_snapshot` is a public method on SDK 0.207.0 (the experimental name survives as an alias) and works live: a container was frozen in 29.2s and a new sandbox booted from the frozen snapshot in 3.6s with state preserved. The S₀ freeze-and-spawn loop is confirmed viable.
2. The `linux-vm` sandbox class is not available on this account in either region (`daytona-vm-*` snapshots carry no regions). Consequently `fork()` is unavailable too — containers reject it with "Forking is not supported for this sandbox". Spawn policy is therefore create-from-S₀ only; the `spawn_mode: fork` path stays in config but is marked unavailable.
3. Container create-from-snapshot latency is 0.5–1.7s. Concurrency probe: at least 6 concurrent sandboxes with no quota error.
4. Volumes are created asynchronously (`pending_create` → `ready` in ~4s); creation must wait for ready before mounting. `VolumeMount` has no read-only flag, so dataset integrity is enforced by checksum re-verification at experiment start.
5. `resize()` is not available for containers (the endpoint 404s). Sandbox size is fixed at creation by choosing the base snapshot (`daytona-small` 1cpu/1GiB/3GiB, `daytona-medium` 2/4/8, `daytona-large` 4/8/10).
6. GPU sandboxes must be ephemeral (`auto_delete_interval=0`), matching the design; actual creation is blocked until the organization wallet has GPU credits. The G2 stage stays dormant until then.
7. Organization/tier endpoints require user auth, not an API key — tier verification is a manual dashboard step, as is checking whether signup credits stack with event credits.
8. Parallel Search round-trip: HTTP 200 with results — the key and the code-absence call site are verified.
9. The generated SDK clients ignore proxy environment variables; on proxied networks every SDK call bypasses the proxy and dies. `repro/orchestrator/daytona_client.py` patches the client configuration to honor `HTTPS_PROXY`/`SSL_CERT_FILE` (a no-op elsewhere).
10. Sandbox exec/file operations travel via the region runtime host (`proxy.app-eu.daytona.io` / `proxy.app-us.daytona.io`), a different domain from the control plane — network policies must allow those hosts too.

## 6. Status

1. Project scaffold in place.
2. Day-0 verification script written and run live; findings above.
3. Orchestrator spine built and unit-tested: append-only ledger (runs/attempts/verdicts/datasets/events), gate state machine (G1/G2/G3), budget ceilings, lifecycle policies with kill switch (delete-by-label, children first).

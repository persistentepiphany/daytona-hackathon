# Preregistered Paper Reproduction on Daytona

A paper's claims become preregistered executable counterfactuals; each runs from the same frozen state with independent lineage on Daytona; the evidence — not the paper's authority — decides the verdict and what gets built.

## 1. How the pipeline works

1. **P0 Intake** — the Planner reads the paper PDF and emits a claims table, an ambiguity ledger (each entry mapped to a config key), and a proposed experiment set drawn from a fixed menu: `reproduce, ablation, stronger_baseline, randomized_control, seed_sweep`. A code-absence certification via Parallel Search records whether any official implementation exists.
2. **G1 Approve & Freeze** — one user action. The orchestrator selects held-out claims (stored in an orchestrator-side annex no agent ever sees), fixes tolerances, writes `prereg.json`, and records its sha256. Nothing downstream may alter it.
3. **P1 Environment archaeology** — the environment is built statefully inside a Daytona sandbox, with every action appended to `RECIPE.sh`, so S₀ ships as both a binary snapshot and a human-readable recipe. A smoke gate (imports, data loader, one fit+predict) must pass; then the sandbox is frozen with `create_snapshot` and a fresh boot from the frozen snapshot must pass the same smoke gate before the archaeology box is deleted.
4. **P2 Experiments** — one sandbox per scientific question, created directly from S₀, seeds looping inside the sandbox. Each run re-verifies dataset checksums against the ledger before compute. Standing controls ride the same queue: a sham twin judged against deterministically corrupted targets (expected NOT REPRODUCED) and a hermeticity run with `network_block_all=True` (expected to complete offline).
5. **P3 Verdict** — a deterministic engine compares evidence to the frozen preregistration; the sealed Verifier role re-derives verdicts from prereg + evidence only and any disagreement is itself a finding. Vocabulary: `REPRODUCED WITHIN TOLERANCE / REPRODUCED OUTSIDE PREREGISTERED TOLERANCE / NOT REPRODUCED / UNDER-CONSTRAINED / NOT ATTEMPTABLE / INCONCLUSIVE`, plus `CONTROL PASS / CONTROL FAIL` for ablations and randomized controls. Held-out claims are scored only here.
6. **P4 Adaptive round (optional)** — at most one, from the same menu, under a prereg-002 document that requires its own approval; rows are labeled ADAPTIVE and cannot alter primary verdicts.
7. **P5 Thin build + G3** — build what survived, not what was claimed: one API endpoint plus one static page in a container sandbox, exposed via a preview link (with a signed URL for sharing). A deterministic fallback builder renders the verdict table with no LLM involved.

## 2. Invariants

1. **S₀ is immutable.** The ledger enforces a single freeze per run; all work happens in sandboxes created from the snapshot.
2. **No sandbox spend before Gate 1.** The lifecycle refuses creation until G1 is approved; GPU classes additionally require G2.
3. **Every manifest is derivable from the frozen preregistration.** The gate is deterministic: wrong hash, claim, type, mutation, seeds, or command → rejected.
4. **The Verifier is sealed.** Its evidence bundle contains prereg, manifests, metrics, leakage and checksums — never source, logs, or history.
5. **Every attempt is replayable from the ledger.** `repro replay --attempt <id>` resolves `S₀ + manifest hash + dataset hashes + command + seeds` with no agent memory involved.
6. **The web exists only upstream of the freeze.** Experiment sandboxes never download; the hermeticity control proves the offline path; Parallel is capped, stage-gated, logged, and never load-bearing.

## 3. Repository layout

1. `repro/orchestrator/` — deterministic core: `ledger.py` (append-only SQLite: runs, attempts, verdicts, datasets, events, gates, budget), `gates.py` (G1/G2/G3/P4), `prereg.py` (freeze + held-out annex), `manifest.py` (deterministic gate), `lifecycle.py` (per-class policies, kill switch), `budget.py` (spend ceilings), `adapter.py` (provider interface), `daytona_client.py` (real adapter + proxy-aware SDK setup), `parallel_client.py` (capped search).
2. `repro/pipeline/` — `p1_archaeology.py`, `staging.py`, `p2_experiments.py`, `runner_files.py`, `p3_verdict.py`, `p4_adaptive.py`, `p5_build.py`, `report.py`.
3. `repro/roles/` — Planner, Implementer, Verifier, Builder over a provider interface; all proposals pass deterministic validation; discrepancy feedback carries direction + magnitude bucket, never raw values.
4. `repro/calibration/fashion_mnist.py` — the hand-written recipe and candidate code proving the loop before any LLM writes code.
5. `papers/fashion-mnist/` — calibration paper metadata, transcribed claims, ambiguity ledger.
6. `scripts/` — `day0_check.py` (live account verification), `run_calibration_p1.py` (prereg → G1 → stage → archaeology → freeze → boot-verify), `run_calibration_p2.py` (experiments + sham + hermeticity → verdicts).
7. `tests/` — 22 unit tests against an in-memory fake adapter; no network needed.

## 4. Setup and usage

1. `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` (Python 3.11+, `daytona==0.207.0` pinned).
2. Environment variables: `DAYTONA_API_KEY` (or `DAYTONA_API`); `PARALLEL_API_KEY` (or `PARALLEL_API`) for the optional search; `ANTHROPIC_API_KEY` only when running the LLM roles.
3. `.venv/bin/pytest` — unit suite.
4. `.venv/bin/python scripts/day0_check.py` — live Day-0 verification (creates and deletes small labeled sandboxes; minimal spend).
5. `.venv/bin/python scripts/run_calibration_p1.py` — freeze prereg, stage data, build and freeze S₀.
6. `.venv/bin/python scripts/run_calibration_p2.py` — run the preregistered experiments, sham, hermeticity; print the verdict table.
7. `repro kill --ledger runs/calibration/ledger.db --run-id <id>` — kill switch (delete by label, children first).
8. `repro replay --ledger runs/calibration/ledger.db --attempt <att-id>` — replay resolution from the ledger.
9. `repro report --run-dir runs/calibration/<run-id> --title "..."` — render the report from persisted artifacts.
10. `repro build --run-dir runs/calibration/<run-id>` — deploy the what-survived page to a sandbox and print the preview URL.

## 5. Day-0 verification (live, against the event account)

1. `create_snapshot` is public API on SDK 0.207.0 and works live: container frozen in 24–36s, fresh sandbox booted from the frozen snapshot in ~4s, **filesystem state preserved across freeze-and-boot** (verified with a marker file in `$HOME`).
2. The `linux-vm` class is unavailable on this account in both regions, so `fork()` is unavailable too (containers reject it: "Forking is not supported for this sandbox"). Spawn policy is create-from-S₀ only — which measured faster than VM forks would plausibly be anyway (0.5–1.7s creates).
3. Auto-pause is VM/Windows-only; container lifecycles use auto-stop + auto-delete + TTL. `resize()` is not served for containers, so sandbox size is fixed at creation by choosing the base snapshot (`daytona-small` 1cpu/1GiB, `daytona-medium` 2/4, `daytona-large` 4/8).
4. Volumes are created asynchronously (`pending_create` → `ready` in ~4s) and must be awaited before mounting; small-file write propagation between sandboxes measured at 0.9s. `VolumeMount` has no read-only flag, so dataset integrity is enforced by checksum re-verification before every run.
5. `network_block_all=True` blocks everything, including package registries — so the hermeticity control is fully establishable on this account. Sandbox egress on the default tier is otherwise restricted to an allowlist (pypi, npm, github, huggingface reachable; UCI, Springer, arXiv reset) — dataset sourcing must fit that world or the org must be verified in the dashboard.
6. GPU sandboxes must be ephemeral (`auto_delete_interval=0`). Creation is refused with "Organization doesn't have GPU credits" in both regions for RTX 5090 and RTX 4090 — even with a positive general credit balance. Daytona tracks GPU credits as a separate wallet line from general credits; allocating them is a dashboard (Wallet page) action the API key cannot perform. G2 stays dormant until that allocation happens; the probe in `scripts/day0_check.py` verifies it in under a minute once done.
7. Concurrency: at least 6 concurrent 1 GiB sandboxes ran without error, but the binding limit is the organization quota of **10 GiB total sandbox memory** — with 4 GiB experiment sandboxes that means two at a time, so the executor runs a quota-aware pool and creates wait patiently for a slot instead of failing.
8. Parallel Search round-trip verified (HTTP 200 with results).
9. The generated SDK clients ignore proxy environment variables; `daytona_client.enable_proxy_env()` patches all three client packages to honor `HTTPS_PROXY`/`SSL_CERT_FILE` (a no-op elsewhere). Control plane is `app.daytona.io`; exec/file operations travel via `proxy.app-eu.daytona.io` / `proxy.app-us.daytona.io`, which restrictive networks must also allow.
10. Sandboxes execute as user `daytona` (`$HOME=/home/daytona`), not root.

## 6. Calibration run (live, run `cal-1788095064`)

1. Calibration paper: Fashion-MNIST (arXiv:1708.07747), chosen because its Table 3 benchmark claims are CPU-reproducible in minutes and its data is reachable from the sandbox egress allowlist. Claims were transcribed from the paper PDF (values cross-checked against the paper's own repository benchmark). Note: this paper has an official implementation — the code-absence wedge criterion is deliberately NOT satisfied by the calibration target; it exists to prove the pipeline, and its rows double as the false-positive check.
2. Pipeline order held: prereg frozen first (`8463f589…`, held-out claim separated into the orchestrator-side annex), four dataset files staged and checksummed (hashes match the publisher's known values), environment built from recipe, smoke gate passed, S₀ frozen in 29s, fresh boot from S₀ re-passed smoke in 12s, archaeology box deleted.
3. Staged data is baked into S₀ from the volume during archaeology, so experiments (including the hermetic one) never read the mount at run time; every run still re-verifies the ledger checksums first.
4. Verdict table (full report with rule ids, attempt ids, and evidence hashes in `results/calibration/cal-1788095064/report.md`):

| Experiment | Claim | Type | Held-out | Reported | Observed | Verdict |
|---|---|---|---|---|---|---|
| SH01 | C4 | sham (corrupted +0.05) | - | 0.561 | 0.5856 | REPRODUCED OUTSIDE PREREGISTERED TOLERANCE |
| SH02 | C1 | sham (corrupted +0.05) | - | 0.848 | 0.8111 | **NOT REPRODUCED** (false-positive check passed) |
| E001 | C1 | reproduce (DecisionTree) | no | 0.798 | 0.8111 | REPRODUCED WITHIN TOLERANCE |
| E002 | C2 | reproduce (RandomForest) | no | 0.873 | 0.8778 | REPRODUCED WITHIN TOLERANCE |
| E003 | C3 | reproduce (LogisticRegression) | no | 0.841 | - | NOT ATTEMPTABLE (exceeded timebox on 2 vCPU) |
| E004 | C4 | reproduce (GaussianNB) | no | 0.511 | 0.5856 | **NOT REPRODUCED** |
| E006 | C7 | reproduce (DecisionTree d50) | no | 0.789 | 0.8008 | REPRODUCED WITHIN TOLERANCE |
| E101 | C2 | ablation (100→10 trees) | no | decrease | 0.8565 | CONTROL PASS (-0.021) |
| E102 | C1 | randomized control (shuffled labels) | no | ~0.10 | 0.1134 | CONTROL PASS |
| E005 | C5 | reproduce (Perceptron) | **yes** | 0.782 | 0.7700 | REPRODUCED WITHIN TOLERANCE |
| A201 | C4 | ADAPTIVE ablation (raw pixels) | - | 0.511 | 0.5856 | NOT REPRODUCED |

5. Hermeticity: **VERIFIED — network_block_all active, run completed**, producing exactly the same mean (0.5856) as the networked E004 from a different sandbox — S₀-rooted runs are bit-for-bit deterministic across sandboxes (SH02 likewise matched E001's 0.8111 exactly).
6. The C4 finding is real science: scikit-learn changed Gaussian smoothing semantics after the paper era (`var_smoothing`, introduced in 0.20, is relative to data variance). The single adaptive round (prereg-002, experiment A201) tested the preregistered ambiguity A2 — raw pixels vs scaled — and produced an identical 0.5856, eliminating preprocessing as the explanation and leaving library-era behavior as the cause. Primary verdicts were untouched, per the invariant.
7. The sham twins demonstrate both edges: SH02 (corrupting a drift-stable claim) fails cleanly as designed; SH01 (corrupting C4 by +0.05) accidentally landed near C4's true modern value — which is why the sham policy now corrupts drift-stable claims.
8. The thin "what survived" app (P5) deploys to a container sandbox with one API endpoint (`/api/verdicts`) plus one static page showing the graded table and run lineage; `scripts/publish_results.py` prints the preview URL and a signed share URL (signed URLs cap at 24 hours — an empirical API limit).

## 7. Architecture v2 additions

1. Binding design document: `ARCHITECTURE.md` (v2); live task checklist and gap analysis in `PROGRESS.md`; user-local handoff notes in `HANDBACK.md`.
2. Claims carry a generalized `condition` object (arbitrary experimental setting); legacy dataset/split claims are normalized automatically. Ambiguity ledger entries are typed: `unstated_choice / equation_ambiguity / version_dependent_default`.
3. Monte Carlo tolerance rule for simulation-table papers: `build_mc_rule` — at least the paper's replication count, tolerance k×SE (default 3), distribution-match never bitwise; the verdict engine refuses under-replicated evidence as INCONCLUSIVE.
4. One validation choke point for agent actions (`repro/orchestrator/actions.py`): agents emit `{"action": "run"|"write"|"search", ...}` only; search routes through the capped Parallel client; the P2 executor imports no model client.
5. Search-on-failure in archaeology (`run_with_recovery`): a repeated error signature earns one environment-mechanics search, then retry; degrades to blind retry with Parallel off.
6. Code delivery is a deterministic tarball at a pinned SHA (`deliver_candidate`), verified after landing — no clone path exists.
7. `data_mode: synthetic`: staging is a no-op and experiments generate data from the manifest's preregistered `condition` (gated like every other manifest field).
8. Ledger-only rerun: the frozen manifest is persisted at execution time; `repro rerun --ledger ... --attempt ... --run-dir ...` reconstructs and re-executes an attempt with no agent memory involved.
9. Intake gates: a paper-class classifier (`1 reported_numbers` proceeds; classes 2–4 decline by name) and a three-outcome code-existence gate (`NOT_FOUND` / `REFERENCED_BUT_DEAD` proceed, `FOUND` declines with the certificate as output) whose certificates carry metadata only — code contents never enter any model context.
10. Local dashboard: `repro dashboard --ledger <db> --evidence-root <dir>` — one stdlib server, one page: run grid, attempt statuses, verdict table, evidence file links.
11. Demo preview lifecycle: `deploy(..., demo_window=True)` keeps the build sandbox on `auto_stop=0` with a 12h TTL; pushes of the output refuse without an explicit G3 approval.
12. `scripts/day0.py` prints a pass/fail Day-0 report; `DAYTONA_LIVE=1 pytest tests/test_live_integration.py` mirrors the archaeology and executor acceptance against the real API (opt-in, run locally).

## 8. Deliberate cuts (do not re-add)

1. Batch-parent launcher (create-from-S₀ replaces it; fork is unavailable on this account anyway).
2. Parallel for ambiguity resolution or any method-semantics lookup — unresolvable gaps become UNDER-CONSTRAINED, which is a finding, not a failure.
3. Skeptic as a runtime agent (fixed menu + one adaptive round instead).
4. Hot snapshots / memory persistence, GROBID/PDF toolchain, predictions files by default, open-ended reasoning checking, multi-paper corpus mode.

## 9. Status

1. Scaffold, Day-0 verification, orchestrator spine — done, unit-tested, verified live.
2. P1 archaeology + S₀ freeze + boot-verify — proven live on the calibration paper.
3. Prereg freeze + held-out annex + manifest gate + P2 executor + sham + hermeticity + P3 verdicts — proven live end-to-end; the full graded verdict table above ran on the event account, with canonical artifacts in `results/calibration/`.
4. P4 adaptive round — executed live (prereg-002, A201) and eliminated a preregistered ambiguity hypothesis.
5. P5 thin build — deployed live to a sandbox with preview + signed URLs.
6. LLM roles (Planner/Implementer/Verifier/Builder) built behind deterministic validation; they require `ANTHROPIC_API_KEY` at runtime and are optional for the deterministic path.
7. G2/GPU dormant pending a GPU-credit allocation on the dashboard Wallet page (separate from the general credit balance; probes confirm the refusal is credit-gated, not capability-gated).
8. Architecture v2 deltas implemented additively and accepted on the fake client (40 tests + 2 live-gated skips); see section 7, `PROGRESS.md`, and `HANDBACK.md`.

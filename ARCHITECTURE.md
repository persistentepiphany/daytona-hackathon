# Preregistered Paper Reproduction on Daytona — Architecture v2

One system statement: **a paper's claims become preregistered executable counterfactuals; each runs from the same frozen state with independent lineage on Daytona; the evidence — not the paper's authority — decides the verdict and what gets built.**

## 1. Invariants

1. **S₀ is immutable.** After freeze, no agent or process ever modifies the canonical snapshot. All work happens in sandboxes created from it.
2. **No sandbox spend before Gate 1.**
3. **Every experiment manifest references the prereg hash.** A deterministic gate rejects any manifest whose metric, tolerance, decision rule, seeds, or condition are not derivable from the frozen preregistration.
4. **The Verifier is sealed.** It sees prereg + evidence only — never implementation source, agent scratchpads, or iteration history. Enforced by a package boundary, not convention.
5. **Every attempt is replayable from the ledger.** `S₀ + manifest + dataset hashes` reconstructs any run. No agent memory is load-bearing.
6. **The web exists only upstream of the freeze.** Parallel is used in P0 and P1 only. Experiment sandboxes are network-restricted; the Verifier never touches the web. The ambiguity ledger is paper-only.

## 2. Components

**Deterministic Orchestrator** (not an LLM): sole holder of keys; owns gates, prereg hashing, sandbox lifecycle, manifest validation, evidence collection, ledger writes, budget counters, kill switch (delete all sandboxes labeled `run=<run_id>`), the policy file (budget caps, experiment menu, `parallel.enabled_stages`, global Parallel off-switch), and the local dashboard.

**Four LLM roles** (agents propose; the orchestrator disposes):

| Role | Sees | Produces | Sandbox access |
|---|---|---|---|
| Planner | paper | paper-class label, claims table, typed ambiguity ledger, experiment set (fixed menu), tolerances, cost estimate | none |
| Implementer | paper method spec, discrepancy packets `{claim_id, direction, magnitude_bucket}` | environment recipe + candidate code as **structured actions** | via orchestrator only |
| Verifier | prereg + evidence files | per-claim verdicts with rule citations | none |
| Builder | validated-knowledge brief only | thin app | build sandbox only |

**Execution model (enforced):** agents return structured actions only — `{"action": "run"|"write"|"search", ...}` — validated at one choke point in the orchestrator. The P2 loop contains zero LLM calls; the Implementer re-enters only between rounds via the discrepancy packet. Executor modules must not import any model client.

Experiment menu (fixed): `{reproduce, ablation, stronger_baseline, randomized_control, seed_sweep}`. Every mutation is a config diff against S₀'s config schema.

## 3. Pipeline and gates

```
P0 Intake ── G1 APPROVE & FREEZE ── P1 Archaeology ── S₀ FREEZE ──
P2 Experiments (primary + calibration + sham) ── P3 Verdict ──
[G2 GPU, only if needed] ── [P4 one adaptive round, optional] ──
P5 Thin build ── G3 PUSH
```

### P0 — Intake
1. **Paper-class classifier** emits `paper_class ∈ {1 reported_numbers, 2 provable_properties, 3 worked_examples, 4 nothing_checkable}`. The pipeline proceeds only for class 1; other classes decline with the class named in the message. No further logic exists for classes 2–4.
2. Planner emits `claims.json` (per claim: `{id, metric, condition, reported_value, source_loc}` — `condition` is an arbitrary experimental-setting object, e.g. `{n, contamination, distribution, replications}`), a **typed** `ambiguity_ledger.json` (`unstated_choice | equation_ambiguity | version_dependent_default`, each mapped to a config key), the proposed experiment set, tolerances, and a cost estimate.
3. **Code-existence gate** (Parallel, one call site), three outcomes: `NOT_FOUND` → proceed; `REFERENCED_BUT_DEAD` → proceed with the dead link recorded on the certificate; `FOUND` → decline, the certificate is the output. Metadata only — URLs, titles, timestamps into `evidence/code_absence.json`; found code contents are never fetched, stored, or passed into any model context. Enforced in the client wrapper.

### G1 — Approve & Freeze
One user action. Orchestrator selects held-out claims (stored orchestrator-side only), fixes tolerances, writes `prereg.json`, records `prereg_hash = sha256(prereg.json)`. Nothing downstream may alter it; follow-ups require a new prereg document.

**Monte Carlo tolerance rule:** for simulation-table papers, experiments run at least the paper's replication count; tolerance = k×SE (k configurable, default 3); reproduction is a distribution-match over the seed sweep, never bitwise.

### P1 — Environment archaeology
Stateful build inside a sandbox; every action appended to `RECIPE.sh` so S₀ ships as binary snapshot + human-readable recipe. **Search-on-failure:** when the same error recurs twice, one Parallel search may resolve environment mechanics (versions, mirrors, build flags) — never method semantics; capped per session; every query logged. Gate: `smoke.sh`. Freeze: `create_snapshot` → snapshot name + git SHA + recipe hash + dataset checksums + prereg_hash recorded. The archaeology box stays as fallback root until one sandbox boots from S₀, then dies.

### Data staging
`data_mode: staged` — one networked container downloads into the `datasets` volume, records sha256 per file, dies; experiments re-verify checksums before compute (no read-only mounts exist).
`data_mode: synthetic` — staging is a no-op; experiments generate data from the manifest's `condition`.

### P2 — Experiments
One sandbox per scientific question, created directly from S₀ (fork only for shared expensive prefixes, where the account supports it). Per-experiment contract:
1. Validate manifest against prereg (deterministic; reject on any mismatch).
2. **Deliver the candidate as a tarball at a pinned SHA** via `fs.upload_file` — never a clone.
3. Verify dataset checksums (staged mode) or accept the condition generator (synthetic mode).
4. Run under a session with heartbeat (`refresh_activity`); seeds loop inside the sandbox.
5. Evidence to `/evidence/{exp_id}/`: `manifest.json`, `metrics.json`, `stdout.log`, `checksums.json`, leakage reports.
6. Leakage checks ride along read-only.
7. `stop()` → auto-delete (interval 0).

Standing controls: **calibration** (expected REPRODUCED), **sham twin** (deterministically corrupted targets, 2-iteration cap, expected NOT REPRODUCED — corrupt a drift-stable claim), **hermeticity** (fresh sandbox from S₀ with `network_block_all=True` + offline env vars; report exactly `VERIFIED — network_block_all active, run completed` or `NOT ESTABLISHED — org tier prevents per-sandbox block-all`).

### P3 — Verdict
Vocabulary: `REPRODUCED WITHIN TOLERANCE · REPRODUCED OUTSIDE PREREGISTERED TOLERANCE · NOT REPRODUCED · UNDER-CONSTRAINED · NOT ATTEMPTABLE · INCONCLUSIVE` (+ `CONTROL PASS/FAIL` for controls). Held-out claims are scored only now. Every verdict cites claim, rule id, attempt ids, evidence hashes. Framing rule, verbatim in the report: *failure to reproduce is evidence the paper as written is insufficient to reconstruct the result — not evidence the authors are wrong.*

### G2 — GPU (only if required)
On-demand, never spot; ephemeral by class (`auto_delete_interval=0`); mount evidence, one-shot run, copy out, stop; tight TTL. GPU credits are a separate wallet line from general credits (dashboard-only allocation).

### P4 — One adaptive round (optional)
Follow-ups from the same menu under `prereg-002`, requiring approval; labeled ADAPTIVE; cannot alter primary verdicts. At most one round.

### P5 — Thin build + G3
Builder sees the validated-knowledge brief only. One API endpoint + one static page in a container sandbox. **Preview lifecycle:** `auto_stop=0` during the demo window, TTL 12h; exposed via `get_preview_link` / `create_signed_preview_url` (signed URLs cap at 24h). No external hosting anywhere. G3 = explicit user approval gates any push.

### Local dashboard
Orchestrator-served, reads the ledger SQLite directly: run grid, per-experiment status, links to evidence files, verdict table. One small server, one page, no hosting, no build framework.

## 4. Lifecycle & budget policy

| Sandbox | auto-stop | auto-delete | TTL | Notes |
|---|---|---|---|---|
| Archaeology | 0 (disabled) | off | 8h | fallback root until S₀ verified |
| Experiment (from S₀) | 0 | 0 (delete on stop) | est×2 | orchestrator stops after evidence pull |
| Fork children | 0 | 0 | est×2 | delete before parent |
| Data stager | 15 | 0 | 2h | network on; no-op in synthetic mode |
| GPU | explicit | 0 (by class) | est×1.5 | on-demand, never spot |
| Build (demo window) | 0 | off | 12h | hosts the preview |

Global: label everything `run=<run_id>`; kill switch = delete by label; per-run spend ceiling; TTL is the budget backstop. Parallel: `enabled_stages: [intake, archaeology]`, per-stage caps, global off-switch; the pipeline completes end-to-end with Parallel disabled.

## 5. Data layout

Volumes: `datasets/` (staged, checksummed); evidence under `/evidence/{exp_id}/`.
Ledger (SQLite, append-only): `runs(run_id, paper_hash, prereg_hash, s0_snapshot, s0_git_sha, recipe_sha, created_at)`, `attempts(attempt_id, run_id, exp_id, claim_id, manifest_hash, spawn_mode, source_ref, sandbox_id, cmd, seeds, started, ended, exit, evidence_sha, cost_est)`, `verdicts(claim_id, run_id, rule_id, observed, delta, verdict, attempt_ids)` plus datasets, events, gates, budget charges.

Experiment manifest (frozen before execution): `{experiment_id, prereg_hash, claim_id, type, condition, mutation, seeds, command, expected_outputs, budget}` — all derivable from the prereg.

## 6. Cut list (deliberate; do not re-add)

1. Batch-parent launcher; Render or any external hosting; quarantine-and-compare.
2. Paper classes 2–4 beyond the label; fetching found code; coding agents inside spine sandboxes.
3. Parallel for ambiguity resolution or method semantics (gaps → UNDER-CONSTRAINED).
4. Skeptic as a runtime agent; hot snapshots; GROBID; predictions files by default; open-ended reasoning checking; multi-paper corpus mode.

## 7. Day-0 verification checklist

1. `create_snapshot` naming on the pinned SDK. 2. `fork()` support and latency vs create-from-S₀; concurrency ceiling. 3. Org tier. 4. What `network_block_all=True` blocks. 5. Declarative image → GPU sandbox. 6. Volume mount semantics and propagation latency. 7. Resource ceilings (`resize` support). 8. Credit stacking. 9. Parallel round-trip.

## 8. Build order

1. Orchestrator spine + FakeDaytona. 2. Schemas + validators. 3. P1 archaeology loop. 4. P2 executor (hand-written manifest first). 5. Implementer agent. 6. Verifier + controls. 7. Dashboard. 8. Intake. 9. Builder + preview + G3. 10. Local handoff (day0 report, live-gated suite, handback).

## 9. Demo screens

1. Paper → proposed contract → Approve & Freeze. 2. Live archaeology → S₀ frozen. 3. Experiment tree from S₀. 4. Evidence per branch (manifest, sandbox id, rule). 5. Verdict table — calibration PASS, sham REJECTED, then graded verdicts. 6. Build what survived → preview URL.

# Progress — architecture v2 deltas

## Gap analysis (repo state vs D1–D11)

| Delta | State before v2 | Work |
|---|---|---|
| D1 condition object | claims carried `dataset`/`split` | ADD: `condition` accepted and normalized; legacy fields still valid; fixtures updated |
| D2 typed ambiguities | untyped entries | ADD: enum `unstated_choice / equation_ambiguity / version_dependent_default`, validator, fixtures typed |
| D3 paper-class classifier | none | ADD: intake classifier + decline path (classes 2–4 label-only) |
| D4 code-existence gate | single-outcome trail existed | ADD: three-outcome gate, metadata-only enforced in wrapper, fixtures for all outcomes |
| D5 MC tolerance | absolute tolerances only | ADD: k×SE rule builder (default k=3), distribution-match semantics, verdict support |
| D6 synthetic data mode | staged-only | ADD: staging no-op path; condition carried in manifest; checksum step skipped |
| D7 tarball delivery | per-file uploads; no clone path existed | ADD: deterministic tar.gz at pinned SHA via fs upload, verified after extract |
| D8 action choke point | recipe/sh + put_file direct | ADD: `{run/write/search}` validation choke point; roles emit actions; P2 verified LLM-import-free |
| D9 preview lifecycle | build auto_stop=30 | ADD: demo-window policy `auto_stop=0`, TTL 12h; preview/signed links already used; no external hosting |
| D10 dashboard | none | ADD: stdlib server over the ledger SQLite — run grid, statuses, evidence links, verdicts |
| D11 cuts | already absent | nothing to do |

Already in place from phase 1 (kept intact, live-proven earlier): ledger/gates/lifecycle/budget/kill switch, prereg + held-out annex + manifest gate, P1 archaeology + smoke + freeze, P2 executor + sham + hermeticity, P3 verdicts + report, P4 single adaptive round, P5 thin build, roles behind deterministic validation, capped Parallel client, 22-test suite.

Note: no `ARCHITECTURE.md` file accompanied the message in this session; the v2 document in the repo root was reconstructed from the v1 text plus deltas D1–D11 and is the binding copy.

## Task checklist

1. [x] T1 — Spine + FakeDaytona surface parity (call-recording fake covers every SDK call the codebase makes; policy file; kill switch test)
2. [x] T2 — Schemas + validators (condition, typed ambiguities, MC tolerance; forged-manifest property tests; stable prereg hash)
3. [x] T3 — P1 action choke point + search-on-failure recovery (two induced failures → one search → recovery scenario on the fake)
4. [x] T4 — P2 executor additions (tarball at SHA, synthetic mode, manifest persisted to ledger, `rerun --attempt`; module imports no model client)
5. [x] T5 — Implementer structured actions + mocked-LLM convergence test (≤3 rounds to within tolerance)
6. [x] T6 — Verifier package boundary check (build fails if verifier imports implementer/executor internals); sham + calibration fixtures
7. [x] T7 — Dashboard (stdlib server over SQLite; grid, statuses, evidence links, verdict table)
8. [x] T8 — Intake (paper-class classifier, three-outcome code gate with fixtures, decline paths, metadata-only certificate)
9. [x] T9 — Builder demo-window lifecycle + G3-gated push refusal
10. [x] T10 — `scripts/day0.py` pass/fail report (user-local), `DAYTONA_LIVE=1` integration suite, `HANDBACK.md`

## Blockers

1. None open. Live-API verification of the new tarball/session/synthetic paths is deferred to the user-local `DAYTONA_LIVE=1` suite by instruction (no network calls from this session in this phase).

## Autonomous driver (branch `auto-driver`) — NOT merged

Status: **not green.** Two full attempts, both short of graded verdicts. The
branch is pushed and left unmerged per the stop rule; `main` is untouched.

### What the autonomous path proved

1. Planner → prereg works. The Planner's proposal converts cleanly into the five
   values `cal.prereg_inputs()` returns; `build_prereg` accepts it and G1 freezes
   a model-written contract. Verified on every attempt.
2. Implementer → S₀ works, including recovery. Run `auto-1788099086` passed the
   smoke gate on round 1. Run `auto-1788099314` failed rounds 1 and 2 and
   recovered on round 3 from the structured feedback alone — the loop, the cap
   and the discrepancy packets all behave as designed.
3. The model-written `train.py` runs correctly inside S₀: the boot check produced
   `{"claim": ..., "value": 0.8112, "n_train": 60000, "n_test": 10000}`.

### Why it is not green

| Run | Rounds | Where it stopped |
|---|---|---|
| `auto-1788099086` | 1 (passed) | P2: implementer keyed `config.json` by its own id `claim_decisiontree_entropy_10` while the prereg used `c1`/`c2`; `train.py --claim c1` failed, no `metrics.json`, both claims NOT ATTEMPTABLE. Fixed (commit 7efd544). |
| `auto-1788099314` | 3 (1,2 failed; 3 passed) | P2: `metrics.json` was produced, then the run died on missing `leakage.json`. P3 then raised `KeyError: 'target'`. |

Round failures inside `auto-1788099314`, both genuine model errors:
1. pinned `numpy==1.26.4`, which has no wheel for the sandbox's Python 3.14, so
   pip tried to compile numpy from source and failed;
2. its own `train.py` crashed on `--set` handling — `json.loads("localdata")`.

### The two open defects

1. **`RUNNER_PY` is not paper-agnostic after all.** It shells out to
   `leakcheck.py`, and the calibration `LEAKCHECK_PY` does `from fashion import
   load_split` — a Fashion-MNIST-specific module name baked into the shared
   runner. The autonomous contract never asks the Implementer for `leakcheck.py`,
   so `leakage.json` is never written and the evidence download fails. Fix:
   require `leakcheck.py` (writing `leakage.json` with `train_test_overlap_rows`,
   `n_train`, `n_test`) as a fourth mandatory file.
2. **Planner rules can omit `target`.** `p3_verdict.judge_experiment` does
   `observed - rule["target"]` and raises `KeyError`. Fix: backfill
   `rule["target"]` from the claim's `reported_value` in
   `repro/auto/contract.py:prereg_inputs`.

Both fixes live in new files only. Neither was attempted: the two-attempt cap
was reached.

### Target caveat

The instruction named a synthetic-data paper. No such paper exists in this
repository — `papers/` contains only `fashion-mnist` — so these runs used the
real Fashion-MNIST text through the autonomous path. That exercises the full
no-hand-written-code path but is **not** a real code-absence pass: Fashion-MNIST
has an official implementation, so P0 returns FOUND and the run proceeds under
the calibration override.

### Update — autonomous path is green (`auto-1788099837`)

Two defects closed after the write-up above: the leakage check no longer imports
a calibration-specific module (`repro/pipeline/runner_files.py` prefers `dataio`
and falls back to `fashion`), and `prereg_inputs` backfills a missing
`rule["target"]`. With those in place the run went straight through on the first
implementer round:

| Stage | Result |
|---|---|
| Planner contract | 2 claims (`dt_fashion_1` 0.873±0.025, `svc_fashion_1` 0.976±0.01), prereg `adc904610e833b6c` |
| Implementer | round 1 smoke gate PASSED; wrote `train.py`, `dataio.py`, `config.json`, `smoke.sh` |
| S₀ | `s0-auto-1788099837`, recipe `6fd074d34471` |
| P2 / P3 | `exp_dt` observed 0.81102, delta −0.06198 → REPRODUCED OUTSIDE PREREGISTERED TOLERANCE |

One row short of a full table: `exp_svc` graded NOT ATTEMPTABLE because two
concurrent experiments raced the ledger's SQLite connection into "cannot commit
- no transaction is active". P2 now runs one experiment at a time (commit
22403a0); the underlying ledger concurrency is still worth fixing.

## Second paper: Best-scored Random Forest (`auto-1788101831`) — FAILED at P1

The first paper other than the calibration target, added with **no code changes**
— `papers/best-scored-rf/` is three data files and nothing else.

### What worked, unassisted

1. The classifier labelled it class 1 (`reported_numbers`) correctly.
2. The Planner read Table 1 of a paper it had never seen, identified BRF as the
   paper's own method, and extracted `monks` 0.6681 and `bcw` 0.972. On the first
   attempt it also used the paper's own reported standard deviations as
   tolerances (0.0024, 0.0104); on the retry it chose looser round numbers
   (0.01, 0.02) — the targets are stable across runs, the tolerances are not.
3. It logged 11 ambiguities, against 3–9 for Fashion-MNIST: the paper is
   materially less specified, which is the correct reading.

### Why it failed

All four implementer rounds died acquiring data from `archive.ics.uci.edu`
(round 1 `curl` exit 35, rounds 2–4 the same host via `requests`). No S₀, no
experiments, no verdicts.

This is documented behavior, not a surprise: README section 3 records that
sandbox egress on the default tier is allowlisted (pypi, npm, github,
huggingface reachable; **UCI**, Springer, arXiv reset), and that Fashion-MNIST
was chosen *because* its data is reachable from that allowlist. The paper was
selected without checking that constraint.

### The real defect this exposed

`repro/auto/build.py:94` calls `implementer.apply_proposal(session, proposal)`
without the `parallel` argument the function accepts. The architecture defines
**search-on-failure** for precisely this case — "when the same error recurs
twice, one Parallel search may resolve environment mechanics (versions,
**mirrors**, build flags)" — so the Implementer should have been able to search
for a reachable mirror after round 2. It could not, and spent rounds 3 and 4
re-trying the same unreachable host with a different HTTP client. Wiring the
Parallel client into the build loop is a one-line fix plus a feedback-packet
change telling the role that searching is available.

Every round's proposed source is committed under
`results/auto/auto-1788101831/candidate/round{1..4}/`.

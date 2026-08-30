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

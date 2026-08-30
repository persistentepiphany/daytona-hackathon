# Handback — architecture v2 phase

## 1. Done

1. All of T1–T9 accepted on the fake client: 40 unit tests green (`pytest -q`), covering the spine, schemas/validators, archaeology recovery, executor contract, implementer convergence, verifier boundary, dashboard queries, intake gates, and the demo preview lifecycle with the gated push.
2. `ARCHITECTURE.md` (v2) at the repo root; `PROGRESS.md` carries the gap analysis and the task checklist; `requirements.txt` pins `daytona==0.207.0`.
3. Deltas D1–D10 implemented additively — every phase-1 code path still works and the phase-1 test suite passes unchanged. D11 (cuts) required no code.
4. Phase-1 live results (calibration verdict table, deployed preview, Day-0 findings) are untouched in `results/` and README sections 5–6.

## 2. How to run the user-local pieces

1. `python scripts/day0.py` — Day-0 checklist items 1–9 with a pass/fail report (creates and deletes small labeled sandboxes; needs `DAYTONA_API_KEY`, optionally `PARALLEL_API_KEY`).
2. `DAYTONA_LIVE=1 DAYTONA_API_KEY=... pytest tests/test_live_integration.py -v` — mirrors the T3 (freeze-and-boot with a surviving marker) and T4 (tarball at pinned SHA, extract, execute) acceptance against the real API; cleans up via the kill switch.
3. `repro dashboard --ledger runs/calibration/ledger.db --evidence-root runs/calibration` — local dashboard on 127.0.0.1:8600.

## 3. Blocked / deferred

1. GPU (G2): creation is refused with "Organization doesn't have GPU credits" in both regions even with a positive general balance — GPU credits are a separate wallet line, dashboard-only. The path is coded and dormant.
2. Live LLM role execution needs `ANTHROPIC_API_KEY` at runtime; every role sits behind deterministic validation and the pipeline completes without them.
3. Live verification of the new v2 paths (tarball delivery, synthetic mode, sessions, demo-window lifecycle) is deferred to the `DAYTONA_LIVE=1` suite per the no-network instruction for this phase.

## 4. Interface assumptions the fake could not verify

1. **Sessions**: the fake models `create_session`/`execute_session_command` as plain exec. The real session API returns command ids and buffered logs; the executor currently uses plain `process.exec` with output redirection, so nothing depends on session semantics yet.
2. **Tarball extraction**: on the fake, `tar -xzf` is a no-op (delivery is verified by SHA round-trip). Live, extraction and in-place execution are covered by `test_t4_tarball_delivery_and_exec`.
3. **`auto_stop=0` semantics**: assumed to mean "auto-stop disabled" per provider docs and phase-1 live behavior on experiment sandboxes; the demo-window build class relies on it.
4. **`update_network_settings` / `refresh_activity` / `pause`**: present on the fake for surface parity; the spine does not call them yet (phase-1 findings: pause is VM/Windows-only on this account; heartbeat was unnecessary at calibration scale).
5. **Preview URL shape**: fake returns a synthetic URL; live URLs are per-sandbox subdomains of the region proxy host (verified in phase 1), and signed URLs cap at 24 hours.
6. **Quota behavior**: the 10 GiB total-memory quota and the 4 GiB size of S₀-derived sandboxes are live findings baked into the executor's quota-aware retry; the fake enforces no quota.
7. **`snapshot.list` pagination and `volume` readiness states** (`pending_create` → `ready`) exist only live; the fake's volumes are always ready.

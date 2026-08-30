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

11. [x] T12 — Live run feed (bus + closed vocabulary + SSE + page), verified live
12. [x] T12b — Completion estimates (measured rates, simulated bands, enforced ceiling)

## T12 / T12b — live feed and completion estimates

Built on `t12-live-feed` off `main`. **Held on the branch, not merged** — acceptance
item 1 is only partially covered by a live recording (see below) and the merge is the
user's call. Additive by construction: `REPRO_TELEMETRY=0` restores pre-feature
behavior exactly, and the suite is 47 pre-existing tests unmodified plus 88 new ones.

### Where the brief and the repository disagreed

1. **There is no `validate_and_execute`.** The action choke point is
   `validate_action` / `apply_action` in `repro/orchestrator/actions.py`. `apply_action`
   is where the producer sits; the dispatch body moved verbatim into `_dispatch` so the
   diff is an insertion rather than a rewrite. `repro/calibration/fashion_mnist.py`
   drives the archaeology session directly rather than through the choke point, so
   `telemetry.tapped_session` wraps it and calls the same tap; a thread-local depth
   guard stops an action arriving through both paths being counted twice.
2. **`cal-1788095064` has no event history to replay.** `ledger.db` and `runs/` are
   gitignored and `scripts/publish_results.py` deliberately does not publish evidence,
   so the published run has prereg, verdicts and report and nothing else. Rather than
   fabricate a stream for it, `scripts/record_calibration.py` records a fresh
   calibration-shaped run live and the feed replays that. The published
   `cal-1788095064` artifacts are untouched.
3. **`runner.sh` is a two-line shim** generated at execution time; the seed loop is
   `RUNNER_PY`. `::progress k/n` therefore lives there — and goes to a side channel
   file, not to stdout. That is stronger than the brief asked for: `stdout.log` is
   hashed into the attempt's evidence, so writing progress to it would have made
   evidence differ between a watched run and an unwatched one. It does not.
4. **The pinned SDK's synchronous `get_session_command_logs` has no callbacks.** The
   async form does, with exactly the `on_stdout`/`on_stderr` signature the brief
   describes, over a websocket. The tap runs it on a private event loop and falls back
   to byte-offset polling of the buffered log if that socket cannot be established, so
   all SDK fragility sits in one function. (That websocket is orchestrator-to-Daytona;
   the page-to-server transport is SSE, as required.)
5. **Fleet ETA is not an event.** The vocabulary is closed and has no kind for it, and a
   derived number that changes every tick has no business in an append-only ledger, so
   it is delivered as a non-ledger SSE `event: estimate` frame computed in the feed
   layer.

### What the live runs caught that the offline suite could not

1. **The generated runner did not compile.** `RUNNER_PY` is a triple-quoted string, so
   the progress line's `\n` became a real newline in the emitted `runner.py`. Nothing
   in the suite executed that generated file, so it reached the sandbox intact and every
   experiment failed there with a missing `metrics.json` — after S₀ had already been
   built. `tests/test_runner_files.py` now compiles both generated sources and runs the
   seed loop against a stub interpreter, including the assertion that stdout is
   byte-identical with and without the progress marker.
2. **`tail`'s default one-second re-check** put chunk delivery at ~740 ms, over the
   latency budget. `-s 0.1` brings it to ~250 ms median.
3. **Progress ran ahead of the work.** The per-seed stdout fallback counted the runner's
   start and finish lines as two seeds, and raced the explicit `::progress` channel. The
   channel now takes precedence once seen, and the fallback counts distinct seeds
   announced minus the one still running — the runner announces a seed *starting*.

### Acceptance

| # | Item | Result |
|---|---|---|
| 1 | Replay renders feed, grid, gates, verdicts | **partial** — see below |
| 2 | Live micro-run, `log.chunk` ≤500 ms, progress per seed | **pass** — 259 ms median, 490 ms max, 4/4 seeds, sandbox deleted |
| 3 | Scripted driver through the real choke point | **pass** — `scripts/feed_driver.py`, all three agent kinds |
| 4 | Kill switch visible ≤2 s | **pass** — 1.31 s |
| 5 | Resume via `Last-Event-ID`, no gaps or duplicates | **pass** — asserted on event ids |
| 6 | Redaction canary; zero credential rows table-wide | **pass** |
| 7 | Verifier seal; no annex content in payloads | **pass** — scope stated below |
| 8 | ETA within ±15% at halfway; ceiling never exceeded | **pass** — 4% out at the halfway mark on the recorded run |
| 9 | Existing suite green and unmodified; off-mode diff test | **pass** |
| 10 | README "Live feed" section; this closeout | **pass** |

On (1), replay is verified end-to-end by `tests/test_replay.py` against two streams
recorded from live Daytona in `fixtures/feed/`: a 98-second archaeology recording
(`agent.action`, `agent.patch` with real diffs, `agent.observation`, `gate.changed`,
`budget.tick`) and the micro-run (`attempt.state`, `attempt.progress`, `log.chunk`,
kill switch). Paced replay reproduces both in order through the real endpoint, a
mid-stream reconnect resumes with no gap or duplicate, and every kind they carry has a
reducer on the page. What is **not** covered by a live recording is `verdict.emitted`:
the calibration recording aborted in `ArchaeologySession.teardown()` on a 404 for an
already-removed sandbox — a pre-existing fragility, unrelated to this feature, which
`scripts/record_calibration.py` now guards around rather than reaching into the
archaeology module for. The run had already frozen S₀ (recipe `e7b334101d3d`, matching
the published calibration) when it stopped. Verdict events are covered by
`tests/test_additivity.py` on a fake run; a recording that carries them wants one more
pass of `scripts/record_calibration.py --profile core`, roughly 20 minutes of live time.

On (7), the assertion is stated precisely rather than loosely: no event of any kind
carries a held-out claim's reported value, tolerance or rule, and across the T12
vocabulary nothing from the annex document appears at all. A held-out experiment is
still visible *as an attempt* — its state and seed progress — because the operator chose
the split at G1 and the feed is theirs; its output streams as a byte count only.

## Blockers

1. None open. Live-API verification of the new tarball/session/synthetic paths is deferred to the user-local `DAYTONA_LIVE=1` suite by instruction (no network calls from this session in this phase).

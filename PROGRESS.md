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

Built on `t12-live-feed` off `main`. **The feed is opt-in** (`REPRO_TELEMETRY=1`), so
merging changes a run's behavior in no way at all until someone asks for it.

### Pre-merge audit

An adversarial pass over the diff before merging found five defects, all now fixed and
covered by tests:

1. **Redaction missed the credential shape this project actually uses.** The lookbehind
   protecting `config_key` also blocked `PARALLEL_API_KEY=`, `ZAI_API=` and
   `Authorization: Bearer …` — every prefixed environment variable, which is all of
   them. `ANTHROPIC_API_KEY=` only appeared to work because its *value* matched the
   `sk-ant-` prefix rule. The pattern is now two alternations: unambiguous words admit a
   leading identifier segment, a bare `key` keeps the lookbehind. Tested with those four
   shapes as positives and `config_key` / `n_estimators` as negatives, plus every
   manifest derivable from a committed preregistration, unchanged.
2. **A failing log tap could leak a billed sandbox.** `tap.close()` ran unguarded in
   `run_experiment`'s `finally`, so a SQLite error inside it skipped `life.stop(sid)`
   *and* replaced the real `ExperimentError`. Now guarded, as the adjacent
   `finish_attempt` already was.
3. **The live feed could drop frames.** The row id was assigned under the ledger lock but
   the fan-out happened outside it, so two threads could deliver out of order; the
   subscriber's cursor is monotonic and its catch-up query resumes past the gap, so the
   lower-numbered event was lost for good. Fan-out now happens inside the lock.
4. **`journal_mode=WAL` was issued before `busy_timeout`.** Converting an existing
   database takes an exclusive lock, so opening a ledger the dashboard held made the
   constructor raise. Order swapped.
5. **The coalescer thread leaked** when `LogTap.start()` failed, because it is
   constructed before the tap starts. Closed in the handler.

Also: `policy.telemetry_enabled` was never called, leaving the policy key inert; the
switch now has one definition that both the policy and the bus use.

### Residual caveats, recorded rather than hidden

1. **WAL is persistent and unconditional.** An existing `ledger.db` is converted in place
   the first time any driver opens it, and recent commits live in a `-wal` sidecar until
   checkpoint — `cp ledger.db` on its own can lose the tail of a run. Kept unconditional
   because a database whose journal mode depends on an environment variable is worse than
   one that is consistently WAL.
2. **Redaction is not behind the flag**, so a feed-off run still rewrites a payload that
   contains a credential. Deliberate: the alternative is that turning the feed off writes
   a leakier table.
3. **The log tap's polling fallback is expensive.** If the SDK websocket is ever
   unavailable, `_poll_logs` re-fetches the whole buffered log every 0.5s and slices by
   offset — quadratic in output size. The websocket path is what live runs actually used.
4. **Held-out verdicts stream**, though their targets and tolerances never do. A
   deliberate call: a verdict is a published output, scored at P3 like every other.

### Concurrency defect fixed in passing

`scripts/auto_run.py` serializes its experiments to one thread with the note *"the
Ledger's SQLite connection is not safe to share across threads … two concurrent
experiments raced it into 'cannot commit - no transaction is active'"*. That is this
branch's `Gates`/`Budget` fix: both were issuing raw `db.execute` on the shared
connection without holding `ledger.lock`, on the hot path of every `Lifecycle.create`.
The cause is fixed; the workaround is left in place deliberately, for whoever owns that
driver to lift when they can verify it.

**Held on the branch, not merged** — acceptance
item 1 is only partially covered by a live recording (see below) and the merge is the
user's call. Additive by construction: `REPRO_TELEMETRY=0` restores pre-feature
behavior exactly, and the suite is 47 pre-existing tests unmodified plus 106 new ones.

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
- no transaction is active". P2 was serialized as a stopgap (commit 22403a0); the
underlying cause was `Budget.charge` reading and writing `ledger.db` outside the
ledger's lock, which is now fixed, along with WAL mode for cross-process runs,
unique run ids, quota-aware creates on every path, and `repro gc` to reclaim the
quota that finished runs hold.

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

## Degraded mode: carry through when data cannot be fetched

Added so a run that cannot reach its data still ends with executable code and a
record, rather than dying at the build gate.

1. `build.py` passes the Parallel client to `apply_proposal`. Necessary but not
   sufficient: the Implementer's own prompt offers only `commands` and `files`,
   and `to_actions` never emits a `search` action, so the wired client is still
   unreachable by the role. Extending the role's contract is the remaining work.
2. After the four normal rounds, one degraded round tells the Implementer to
   generate data from the claim's condition instead of downloading it.
3. A degraded run's rows are relabelled `NOT COMPARABLE - synthetic data
   substitute`, keeping the engine's grading under `graded_verdict_withheld`.
   Numbers measured against generated data are not a reproduction.
4. A run that never passes the gate now still writes `verdicts.json` (all
   NOT ATTEMPTABLE) and `report.md`, so the candidate source always ships with a
   record of what happened.

### Evidence

`auto-1788102379` proved the mechanism: rounds 1–4 failed on the unreachable UCI
host, the degraded round passed the smoke gate, S₀ froze, and both experiments
executed at mean 0.433333. It then died in P3 on a separate defect.

`auto-1788104282-24d415` is the committed deliverable: rounds 1–4 failed on the
same host, the degraded round produced code whose own smoke check failed on a
malformed `config.json`, and the run still wrote a prereg, a report, two
NOT ATTEMPTABLE verdicts and all five rounds of source under `candidate/`.

### The planner-output fragility this exposed

Four consecutive runs crashed in `p3_verdict` on four variants of one problem:
a rule with a string target, a rule with no target, a rule with no tolerance,
and the word `"mean"` in a numeric field. Every crash landed after the
experiments had run. `_complete_rule` now reads each number defensively and
refuses to freeze an ungradeable prereg, so a malformed rule costs seconds
rather than a whole run. This is the most likely thing to break on a new paper.

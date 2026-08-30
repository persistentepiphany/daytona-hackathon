# Preregistered Paper Reproduction on Daytona

A paper's claims become preregistered executable counterfactuals; each runs from the same frozen state with independent lineage on Daytona; the evidence — not the paper's authority — decides the verdict and what gets built.

## 1. How the pipeline works

The hosted control plane accepts arXiv IDs/URLs and direct PDF uploads. An arXiv
ID is the preferred hosted path: Render retrieves metadata through the arXiv API
and downloads the matching PDF server-side. Production stores state in Postgres
and dispatches work through RQ/Render Key Value. Until S3 credentials are added,
PDFs, extracted text, and evidence use a shared Postgres blob table with a 72-hour
TTL; this is temporary staging storage, not an archival store.
Render coordinates work; the P1/P2 compute itself runs in isolated Daytona
sandboxes. A worker restart recovers the same public job as a new recorded
attempt instead of changing it to `interrupted`.

1. **P0 Intake** — the Planner reads the paper PDF and emits a claims table, an ambiguity ledger (each entry mapped to a config key), and a proposed experiment set drawn from a fixed menu: `reproduce, ablation, stronger_baseline, randomized_control, seed_sweep`. A code-absence certification via Parallel Search records whether any official implementation exists.
2. **G1 Approve & Freeze** — one user action. The orchestrator selects held-out claims (stored in an orchestrator-side annex no agent ever sees), fixes tolerances, writes `prereg.json`, and records its sha256. Nothing downstream may alter it.
3. **P1 Environment archaeology** — the environment is built statefully inside a Daytona sandbox, with every action appended to `RECIPE.sh`, so S₀ ships as both a binary snapshot and a human-readable recipe. A smoke gate (imports, data loader, one fit+predict) must pass; then the sandbox is frozen with `create_snapshot` and a fresh boot from the frozen snapshot must pass the same smoke gate before the archaeology box is deleted.
4. **P2 Experiments** — one sandbox per scientific question, created directly from S₀, seeds looping inside the sandbox. Each run re-verifies dataset checksums against the ledger before compute. Standing controls ride the same queue: a sham twin judged against deterministically corrupted targets (expected NOT REPRODUCED) and a hermeticity run with `network_block_all=True` (expected to complete offline).
5. **P3 Verdict** — a deterministic engine compares evidence to the frozen preregistration; the sealed Verifier role re-derives verdicts from prereg + evidence only and any disagreement is itself a finding. Vocabulary: `REPRODUCED WITHIN TOLERANCE / REPRODUCED OUTSIDE PREREGISTERED TOLERANCE / NOT REPRODUCED / UNDER-CONSTRAINED / NOT ATTEMPTABLE / INCONCLUSIVE`, plus `CONTROL PASS / CONTROL FAIL` for ablations and randomized controls. Held-out claims are scored only here.
6. **P4 Adaptive round (optional)** — at most one, from the same menu, under a prereg-002 document that requires its own approval; rows are labeled ADAPTIVE and cannot alter primary verdicts.
7. **P5 Thin build + G3** — build what survived, not what was claimed: one API endpoint plus one static page in a container sandbox, exposed via a preview link (with a signed URL for sharing). A deterministic fallback builder renders the verdict table with no LLM involved.
8. **Private GitHub publication** — after the run has a terminal record, an explicit
   `POST /runs/{job_id}/gates/G3/approve` creates or updates a private repository
   under `persistentepiphany` using a GitHub App user token. PDFs, datasets, secrets,
   and oversized logs are excluded from the atomic evidence commit.

### Hosted API

- `POST /papers/arxiv` with `{ "arxiv_id_or_url": "1708.07747" }`
- `POST /papers/uploads` → direct S3 PUT when configured, or a temporary shared
  upload endpoint for PDFs up to 4 MiB → `POST /papers/uploads/{id}/complete`
- `GET /papers/{paper_id}` to follow ingestion and extraction
- `POST /runs` with `{ "paper_id": "...", "seeds": "17,41,93" }`
- `GET /runs/{job_id}` and `GET /runs/{job_id}/events` for persisted status/SSE
- `POST /runs/{job_id}/gates/G3/approve` for explicit private GitHub publication

Production deployment is described by `render.yaml` and is deliberately pinned
to `feat/arxiv-e2e-pipeline` for staging. The web service and worker share
Postgres and Key Value. The staging Blueprint sets
`OBJECT_STORAGE_BACKEND=database`; expired arXiv objects are refetched when the
same ID is submitted again. To move to permanent storage, set the backend to
`s3` and supply the S3-compatible settings shown in `.env.example`.

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
7. `repro/telemetry.py`, `repro/logtap.py`, `repro/feed.py`, `repro/estimates.py` — the live feed (section 10): the event bus and its single redaction site, the sandbox log tap, the SSE endpoint and page, and the completion estimates.
8. `tests/` — unit tests against an in-memory fake adapter plus env-key fallback; no network needed.

## 4. Setup and usage

1. `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` (Python 3.11+, `daytona==0.207.0` pinned).
2. Environment variables: `DAYTONA_API_KEY` (or `DAYTONA_API`); `PARALLEL_API_KEY` (or `PARALLEL_API`) for the optional search; `ANTHROPIC_API_KEY` only when running the LLM roles. If a variable is unset, the same names are read from a `.env` file in the current directory or the repository root.
3. `.venv/bin/pytest` — unit suite.
4. `.venv/bin/python scripts/day0_check.py` — live Day-0 verification (creates and deletes small labeled sandboxes; minimal spend).
5. `.venv/bin/python scripts/run_calibration_p1.py` — freeze prereg, stage data, build and freeze S₀.
6. `.venv/bin/python scripts/run_calibration_p2.py` — run the preregistered experiments, sham, hermeticity; print the verdict table.
7. `repro kill --ledger runs/calibration/ledger.db --run-id <id>` — kill switch (delete by label, children first).
8. `repro replay --ledger runs/calibration/ledger.db --attempt <att-id>` — replay resolution from the ledger.
9. `repro report --run-dir runs/calibration/<run-id> --title "..."` — render the report from persisted artifacts.
10. `repro build --run-dir runs/calibration/<run-id>` — deploy the what-survived page to a sandbox and print the preview URL.
11. `.venv/bin/python scripts/fanout.py --all --concurrency 2` — run every paper under `papers/` through the autonomous pipeline at once (one process per paper) and print one verdict table per paper. `--gc-first` reclaims quota before launching; `--base-snapshot daytona-small` trades sandbox size for a third concurrent slot.
12. `repro gc --dry-run` — show the org quota held by finished runs (stale `s0-*` snapshots and idle preview sandboxes); drop `--dry-run` to reclaim it. `--keep-run` pins a run whose S₀ must stay replayable, `--keep-previews N` keeps the newest N demo URLs alive.

### Running several papers at once

One OS process per paper is the supported shape: run ids carry a random suffix so
two launches in the same second cannot collide, the shared ledger runs in WAL mode
with a 30s busy timeout, and every sandbox create queues on a quota refusal instead
of failing the run. The binding limit is the **org quota, not the code**: 10 GiB
total sandbox memory against 4 GiB per `daytona-medium` S₀ box means two pipelines
in flight (three on `daytona-small`). Two things silently spend that quota after a
run ends — the `s0-<run_id>` snapshot each run freezes (~14.5 GB of registry
storage apiece) and the P5 preview sandbox, which `kill_stray.py` deliberately
preserves — so `repro gc` is what keeps the ceiling from creeping down over a day
of runs.

13. `repro feed --ledger <ledger.db> --run-id <id>` — the live feed on `127.0.0.1:8700` (section 10). Add `--replay paced --speed 4` to play a finished run back.
14. `REPRO_TELEMETRY=1` — turn the live feed on for a run. **It is off by default**, and off means off: no feed events, no sandbox log tap, no extra provider calls. The feed's own scripts set it themselves, so only a hand-run pipeline needs to. Exactly what does and does not differ is stated in section 10.

### Render keepalive

The Render API exposes a fast `GET /healthz` endpoint. The scheduled GitHub Actions
workflow in `.github/workflows/render-keepalive.yml` calls it every five minutes,
which prevents an idle service from being suspended. It can also be run manually from
the Actions tab. By default it targets `https://daytona-repro-api.onrender.com/healthz`;
set the repository variable `RENDER_HEALTHCHECK_URL` to point at a renamed or different
Render service without changing code.

## 5. Day-0 verification (live, against the event account)

1. `create_snapshot` is public API on SDK 0.207.0 and works live: container frozen in 24–36s, fresh sandbox booted from the frozen snapshot in ~4s, **filesystem state preserved across freeze-and-boot** (verified with a marker file in `$HOME`).
2. The `linux-vm` class is unavailable on this account in both regions, so `fork()` is unavailable too (containers reject it: "Forking is not supported for this sandbox"). Spawn policy is create-from-S₀ only — which measured faster than VM forks would plausibly be anyway (0.5–1.7s creates).
3. Auto-pause is VM/Windows-only; container lifecycles use auto-stop + auto-delete + TTL. `resize()` is not served for containers, so sandbox size is fixed at creation by choosing the base snapshot (`daytona-small` 1cpu/1GiB, `daytona-medium` 2/4, `daytona-large` 4/8).
4. Volumes are created asynchronously (`pending_create` → `ready` in ~4s) and must be awaited before mounting; small-file write propagation between sandboxes measured at 0.9s. `VolumeMount` has no read-only flag, so dataset integrity is enforced by checksum re-verification before every run.
5. `network_block_all=True` blocks everything, including package registries — so the hermeticity control is fully establishable on this account. Sandbox egress on the default tier is otherwise restricted to an allowlist (pypi, npm, github, huggingface reachable; UCI, Springer, arXiv reset) — dataset sourcing must fit that world or the org must be verified in the dashboard.
6. GPU sandboxes must be ephemeral (`auto_delete_interval=0`). Creation is refused with "Organization doesn't have GPU credits" in both regions for RTX 5090 and RTX 4090 — even with a positive general credit balance. Daytona tracks GPU credits as a separate wallet line from general credits; allocating them is a dashboard (Wallet page) action the API key cannot perform. G2 stays dormant until that allocation happens; the probe in `scripts/day0_check.py` verifies it in under a minute once done.
7. Concurrency: at least 6 concurrent 1 GiB sandboxes ran without error, but the binding limit is the organization quota of **10 GiB total sandbox memory** — with 4 GiB experiment sandboxes that means two at a time, so every create (P1's archaeology box included) waits patiently for a slot instead of failing. Snapshots and preview sandboxes hold that quota indefinitely once a run ends, which is what `repro gc` reclaims.
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

## 10. Live feed

The dashboard in section 3 reads the ledger after the fact. The feed shows a run while it
happens: what the agents are doing, what they are building, and how much longer the
work can take.

It is **opt-in**. With `REPRO_TELEMETRY` unset a run behaves exactly as it did before
the feed existed — the bus emits nothing, and the executor starts no log tap, so a
sandbox sees no extra sessions and no marker file. `REPRO_TELEMETRY=1` turns it on;
`scripts/feed_driver.py`, `scripts/live_microrun.py` and `scripts/record_calibration.py`
do that for themselves.

### Opening it

1. `repro feed --ledger runs/<...>/ledger.db --run-id <id>` then open
   `http://127.0.0.1:8700/?run_id=<id>`. It binds loopback only — no auth layer, no
   framework, no websockets between page and server, and no hosting.
2. Drivers can serve it inside their own process instead, so a live run and the page
   watching it share one bus and one port: `scripts/record_calibration.py` and
   `scripts/live_microrun.py` both do, and print the URL on startup.
3. `scripts/feed_driver.py` needs neither a sandbox nor a model key: it pushes scripted
   `write`/`run` actions through the real action choke point, so what appears in the
   browser is produced by the same code path a real run uses.

### From the run API

`server.py` runs each reproduction in its own process, so a run started over HTTP can be
watched while it happens:

1. `POST /runs` as usual — the worker now sets `REPRO_TELEMETRY=1` for the run it spawns.
2. `GET /runs/{job_id}` reports a `feed_url`.
3. Open `GET /runs/{job_id}/feed` in a browser. It streams from
   `GET /runs/{job_id}/events`, which tails that run's ledger — the same path replay
   uses, because the run is in a different process and there is no shared bus.

Opening the feed before the run has picked a run id waits rather than failing.

### Replay mode

`--replay paced --speed N` (or `?replay=paced&speed=N` on the URL) replays a finished
run at its recorded pace, divided by N. Replay is not a test fixture — it is a
first-class way to watch a run, and it exercises the same endpoint, reducers and page as
a live one. A finished run's ledger is all it needs, so a run recorded on one machine
plays back on another.

### The two bar styles

The timing strip distinguishes what was measured from what is merely bounded, because
conflating the two is how an honest progress display starts lying:

1. **Solid bar — measured.** Completed attempts out of planned, and a fleet band from a
   queue simulation over the pool width using the median duration of attempts that have
   actually finished (this run first, then ledger history for the same paper, then the
   configured default). It is shown as a range, never a single number, and it is
   labelled with how many samples it rests on. With no measurements yet, the band is
   withheld entirely rather than filled in from a config value.
2. **Hollow bar — the ceiling.** Not an estimate at all: the sum of remaining sandbox
   TTLs, bounded by the run's remaining budget. Sandboxes are charged for their whole
   TTL before they are created and the budget refuses to overspend, so the run cannot
   cross this line. It is always displayed.

Per-attempt progress is `elapsed/k × (n−k)` over that attempt's own completed seeds — a
measured rate, reported only once at least one seed has finished. While an implementer
round is outstanding no whole-run completion time is shown at all, because the number of
further rounds is not knowable; LLM turns show elapsed time only.

### What is and is not identical with the feed off

Precisely, because "additive" is worth stating exactly rather than loosely:

1. **Evidence is byte-identical.** `manifest.json`, `metrics.json`, `stdout.log`,
   `leakage.json`, `checksums.json` and the `evidence_sha` derived from them are the same
   bytes either way — progress goes to its own side-channel file that is never collected,
   and the executor's command, redirect and environment are untouched.
   `tests/test_additivity.py` compares two runs file by file, and asserts the sandbox
   call surface is identical too.
2. **Ledger rows are identical**, in every table, with the events table carrying only
   the pre-existing kinds.
3. Three things do differ, none of which changes an output. The `events` table gains an
   index. Its payloads pass through redaction whether the feed is on or off — deliberate,
   since a run with the feed off must not write a leakier table than one with it on, and
   a no-op unless a payload actually contains a credential. And the ledger is converted
   to SQLite's WAL journal mode, which is persistent in the file: copy a `ledger.db`
   without its `-wal` sidecar and you can lose the tail of a run.

### What the feed will not carry

Events carry display tails, never canonical artifacts: a patch event carries the head of
a hunk and the path of the full diff on disk, an observation carries an output tail. The
evidence files remain the only source of truth.

Redaction (`sk-ant-*`, `ghp_*`, `github_pat_*`, and generic `key`/`token`/`secret`
assignments) happens inside `emit()` and nowhere else, so no call site can leak by
forgetting to scrub. It is not behind the on/off flag — a run with the feed off must not
write a leakier events table than one with it on.

The annex's contents never appear: no event of any kind carries a held-out claim's
reported value, its tolerance or its decision rule. Being precise about what the feed
does show — a held-out experiment appears as an attempt, with its state and its seed
progress, since the operator chose the split at G1 and the feed is theirs — but its
output is replaced by a byte count, because its stdout carries the observed value the
annex exists to withhold. Held-out claims are still scored only at P3, exactly as before.

The verifier is visible only as an `agent.action` saying its evidence bundle was
delivered, and as the verdicts it produced.

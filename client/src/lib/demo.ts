/**
 * Demo fixture: the real committed Fashion-MNIST run (results/auto/auto-1788099837),
 * replayed on a timer so the pipeline animates without spending Daytona minutes.
 *
 * Every value below is copied from that run's verdicts.json and report.md — the
 * timing is the only fiction.
 */

const RUN_ID = "auto-1788099837";
const PREREG = "adc904610e833b6c18c5c0bfb4438a33d730695ce4bfacc11efb93a0faf79462";

export const DEMO_JOB_ID = "demo-fashion-mnist";
export const DEMO_PAPER_DIRS = [
  "papers/best-scored-rf",
  "papers/dnn-pattern-recognition",
  "papers/fashion-mnist",
];

/** Log lines in pipeline order, each with the second it surfaces at. */
const SCRIPT: Array<[number, string]> = [
  [0, "P0 paper class empirical-benchmark (calibration)"],
  [1, "P0 code gate FOUND -> calibration paper proceeds under override"],
  [2, "planner: 2 claims, 2 experiments, 3 ambiguities"],
  [3, "contract: claims ['dt_fashion_1', 'svc_fashion_1'] targets [0.873, 0.897] tolerances [0.02, 0.02]"],
  [4, `G1 prereg frozen ${PREREG.slice(0, 16)} (seeds [17, 41, 93])`],
  [5, "P1 implementer build loop (cap 4 rounds)"],
  [6, "P1 round 1: environment recipe accepted, smoke gate PASSED"],
  [8, `P1 S0 frozen s0-${RUN_ID} (recipe 6fd074d34471)`],
  [10, "P2 exp_dt mean=0.81102"],
  [12, "P2 exp_svc FAILED: solver did not converge within the preregistered budget"],
  [13, "P3 exp_dt dt_fashion_1 observed=0.81102 -> REPRODUCED OUTSIDE PREREGISTERED TOLERANCE"],
  [14, "P3 exp_svc svc_fashion_1 observed=None -> NOT ATTEMPTABLE"],
  [15, `done: runs/auto/${RUN_ID}`],
];

export const DEMO_TOTAL_MS = 16_000;

const VERDICTS = {
  run_id: RUN_ID,
  prereg_hash: PREREG,
  framing:
    "failure to reproduce is evidence the paper as written is insufficient to reconstruct the result - not evidence the authors are wrong",
  degraded: false,
  verdicts: [
    {
      experiment_id: "exp_dt",
      claim_id: "dt_fashion_1",
      rule_id: "rule_dt",
      type: "reproduce",
      observed: 0.81102,
      delta: -0.06198,
      verdict: "REPRODUCED OUTSIDE PREREGISTERED TOLERANCE",
      held_out: false,
    },
    {
      experiment_id: "exp_svc",
      claim_id: "svc_fashion_1",
      rule_id: "rule_svc",
      type: "reproduce",
      observed: null,
      delta: null,
      verdict: "NOT ATTEMPTABLE",
      held_out: false,
    },
  ],
};

export const DEMO_REPORT = [
  "# Reproduction report: Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms",
  "",
  "*failure to reproduce is evidence the paper as written is insufficient to reconstruct the result - not evidence the authors are wrong*",
  "",
  "## Run lineage",
  "",
  "1. Run id: `auto-1788099837`",
  "2. Preregistration hash: `adc904610e833b6c18c5c0bfb4438a33d730695ce4bfacc11efb93a0faf79462`",
  "3. Frozen snapshot S0: `s0-auto-1788099837` (recipe `6fd074d34471dfb2600f17220e74172462ce335a03e35e247f421b5a55c5b0ed`, git `ade625b1ab11e68ac8fb86e0fc774211d9acc9be`)",
  "4. Paper hash: `253b6ef70144cb56d13b4e67d3f8cafad42df78d5984265e0a6e4812ad1715ae`",
  "",
  "## Controls (scored before the target rows)",
  "",
  "1. Calibration: this run is itself the calibration paper run.",
  "2. Hermeticity: NOT RUN - autonomous smoke path",
  "",
  "## Primary preregistered results",
  "",
  "| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict | Rule | Attempts | Evidence |",
  "|---|---|---|---|---|---|---|---|---|---|",
  "| exp_dt | dt_fashion_1 | reproduce | no | 0.81102 | -0.06198 | **REPRODUCED OUTSIDE PREREGISTERED TOLERANCE** | rule_dt | att-7c97ea7a | cfa40dcef387 |",
  "| exp_svc | svc_fashion_1 | reproduce | no | None | None | **NOT ATTEMPTABLE** | rule_svc |  |  |",
  "",
  "## Code-absence certification",
  "",
  "1. Status: COMPLETED",
  "2. GitHub - zalandoresearch/fashion-mnist - https://github.com/zalandoresearch/fashion-mnist",
  "3. Fashion MNIST - https://www.kaggle.com/datasets/zalando-research/fashionmnist",
  "4. [1708.07747] Fashion-MNIST - https://arxiv.org/abs/1708.07747",
  "5. fashion_mnist - Datasets - https://www.tensorflow.org/datasets/catalog/fashion_mnist",
].join("\n");

export function isDemoMode(): boolean {
  if (typeof window === "undefined") return false;
  const q = new URLSearchParams(window.location.search).get("demo");
  if (q === "1" || q === "true") {
    window.localStorage.setItem("repro:demo", "1");
    return true;
  }
  if (q === "0" || q === "false") {
    window.localStorage.removeItem("repro:demo");
    return false;
  }
  return window.localStorage.getItem("repro:demo") === "1";
}

/** The demo job as the Render API would have returned it at `elapsedMs` into the run. */
export function demoJob(elapsedMs: number | null): Record<string, unknown> {
  // null = the run has not been started yet in this session
  const elapsed = elapsedMs == null ? DEMO_TOTAL_MS : elapsedMs;
  const secs = elapsed / 1000;
  const logs = SCRIPT.filter(([at]) => at <= secs).map(
    ([at, line]) => `[00:${String(Math.floor(at / 60)).padStart(2, "0")}:${String(at % 60).padStart(2, "0")}] ${line}`,
  );
  const finished = elapsed >= DEMO_TOTAL_MS;
  return {
    job_id: DEMO_JOB_ID,
    status: finished ? "succeeded" : "running",
    paper_dir: "papers/fashion-mnist",
    seeds: "17,41,93",
    publish: true,
    created_at: Date.now() / 1000 - secs,
    run_id: RUN_ID,
    exit_code: finished ? 0 : null,
    log_tail: logs,
    verdicts: finished ? VERDICTS : null,
    has_report: finished,
    degraded: false,
    duration_s: finished ? 421.7 : Math.round(secs),
    preview_url: finished
      ? "https://8000-c0110205-5d52-405a-ba61-6e870fec54a7.daytonaproxy01.eu"
      : null,
  };
}

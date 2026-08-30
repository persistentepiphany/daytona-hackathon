/** Client for the Render repro API — browser talks to /api/*, proxy strips prefix + injects bearer. */
import {
  DEMO_CODE,
  DEMO_CODE_AT_MS,
  DEMO_JOB_ID,
  DEMO_REPO,
  DEMO_PAPER_DIRS,
  DEMO_REPORT,
  DEMO_TOTAL_MS,
  demoJob,
  isDemoMode,
} from "./demo";

/** When the demo run was started this session; null means it has not been triggered yet. */
let demoStartedAt: number | null = null;

export type PaperSummary = {
  slug: string;
  title: string;
  paper_dir: string;
  arxiv_id?: string;
  authors: string[];
  ready: boolean;
  chars: number;
};

export type RunSummary = {
  job_id: string;
  run_id?: string | null;
  status: string;
  title: string;
  paper_slug?: string | null;
  paper_dir?: string;
  created_at: string;
  source?: string;
};

export type StageState = {
  status: "pending" | "running" | "done" | "failed" | string;
  detail: string;
};

export type VerdictRow = {
  experiment_id: string;
  claim_id: string;
  rule_id?: string;
  type?: string;
  observed: number | null;
  delta: number | null;
  verdict: string;
  held_out?: boolean;
};

export type RunDetail = RunSummary & {
  message?: string;
  exit_code?: number | null;
  logs: string[];
  stages: Record<string, StageState>;
  verdicts: {
    run_id: string;
    prereg_hash?: string;
    verdicts: VerdictRow[];
    framing?: string;
    degraded?: boolean;
  } | null;
  report?: string | null;
  code?: Array<{ name: string; body: string; url: string }>;
  repo?: typeof DEMO_REPO | null;
  error?: string | null;
  preview_url?: string | null;
  degraded?: boolean;
  updated_at?: string;
};

const STAGE_ORDER = ["intake", "planner", "freeze", "build", "experiments", "verdicts"] as const;

const PAPER_TITLES: Record<string, string> = {
  "fashion-mnist": "Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms",
  "best-scored-rf": "Best-scored Random Forest Classification",
  "dnn-pattern-recognition": "Deep Neural Networks for Pattern Recognition",
};

const ALIASES: Record<string, string> = {
  "fashion-mnist": "papers/fashion-mnist",
  "fashion mnist": "papers/fashion-mnist",
  "1708.07747": "papers/fashion-mnist",
  zalando: "papers/fashion-mnist",
  "best-scored-rf": "papers/best-scored-rf",
  "best-scored": "papers/best-scored-rf",
  "1905.11028": "papers/best-scored-rf",
  "random forest": "papers/best-scored-rf",
};

export function isActiveStatus(status: string): boolean {
  return status === "queued" || status === "running";
}

export function isTerminalStatus(status: string): boolean {
  return !isActiveStatus(status);
}

async function parse<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      (data as { detail?: string; error?: string }).detail ||
      (data as { error?: string }).error ||
      `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data as T;
}

function emptyStages(): Record<string, StageState> {
  return Object.fromEntries(STAGE_ORDER.map((k) => [k, { status: "pending", detail: "" }]));
}

function applyLogLine(stages: Record<string, StageState>, line: string): void {
  const plain = line.replace(/^\[\d{2}:\d{2}:\d{2}\]\s*/, "");

  const mark = (key: string, status: string, detail: string) => {
    stages[key] = { status, detail: detail.slice(0, 180) };
    for (const earlier of STAGE_ORDER) {
      if (earlier === key) break;
      if (stages[earlier].status === "pending") {
        stages[earlier] = { status: "done", detail: stages[earlier].detail || "ok" };
      }
    }
  };

  if (plain.startsWith("P0")) {
    mark("intake", plain.includes("FAILED") ? "failed" : "running", plain);
    if (plain.includes("code gate")) mark("intake", "done", plain);
  } else if (plain.startsWith("planner:") || plain.startsWith("contract:")) {
    mark("planner", plain.startsWith("contract:") ? "done" : "running", plain);
  } else if (plain.startsWith("G1")) {
    mark("freeze", "done", plain);
  } else if (plain.startsWith("P1") || plain.includes("round ")) {
    const status = plain.includes("FAILED")
      ? "failed"
      : plain.includes("S0 frozen") || plain.includes("smoke gate PASSED")
        ? "done"
        : "running";
    mark("build", status, plain);
  } else if (plain.startsWith("P2")) {
    mark("experiments", "running", plain);
  } else if (plain.startsWith("P3")) {
    mark("verdicts", "running", plain);
  } else if (plain.startsWith("done:")) {
    for (const k of STAGE_ORDER) {
      if (stages[k].status === "pending" || stages[k].status === "running") {
        stages[k] = { status: "done", detail: stages[k].detail || "ok" };
      }
    }
  }
}

export type FeedEvent = {
  at: string;
  stage: string;
  label: string;
  text: string;
  level: "ok" | "error" | "info";
};

const FEED_LABELS: Record<string, string> = {
  intake: "Intake",
  planner: "Planner",
  freeze: "Prereg",
  build: "Implementer",
  experiments: "Experiment",
  verdicts: "Judge",
  done: "Done",
};

function stageForLine(plain: string): string {
  if (plain.startsWith("P0")) return "intake";
  if (plain.startsWith("planner:") || plain.startsWith("contract:")) return "planner";
  if (plain.startsWith("G1")) return "freeze";
  if (plain.startsWith("P1") || plain.includes("round ")) return "build";
  if (plain.startsWith("P2")) return "experiments";
  if (plain.startsWith("P3")) return "verdicts";
  if (plain.startsWith("done:")) return "done";
  return "info";
}

/** Turns raw `log_tail` lines — from the live API or the demo — into feed rows. */
export function parseFeed(logs: string[]): FeedEvent[] {
  return logs.map((line) => {
    const stamp = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*/);
    const plain = line.replace(/^\[\d{2}:\d{2}:\d{2}\]\s*/, "");
    const stage = stageForLine(plain);
    // "NOT REPRODUCED" contains "REPRODUCED", so negatives must be tested first
    const level: FeedEvent["level"] =
      /failed|NOT REPRODUCED|NOT ATTEMPTABLE|OUTSIDE PREREGISTERED|error/i.test(plain)
        ? "error"
        : /PASSED|frozen|done:|FOUND|REPRODUCED WITHIN/.test(plain)
          ? "ok"
          : "info";
    return {
      at: stamp ? stamp[1] : "",
      stage,
      label: FEED_LABELS[stage] || "Log",
      text: plain.replace(/^(P\d|G1)\s*/, ""),
      level,
    };
  });
}

function stagesFromLogs(logs: string[], status: string): Record<string, StageState> {
  const stages = emptyStages();
  for (const line of logs) applyLogLine(stages, line);
  if (isTerminalStatus(status) && status !== "interrupted") {
    for (const k of STAGE_ORDER) {
      if (stages[k].status === "pending" || stages[k].status === "running") {
        const failed = status === "failed" || status === "build_failed";
        stages[k] = {
          status: failed && k === "build" ? "failed" : "done",
          detail: stages[k].detail || status,
        };
      }
    }
  }
  return stages;
}

function titleForDir(paperDir?: string | null): string {
  if (!paperDir) return "Reproduction run";
  const slug = paperDir.replace(/^papers\//, "");
  return PAPER_TITLES[slug] || slug;
}

function slugFromDir(paperDir?: string | null): string | null {
  if (!paperDir) return null;
  return paperDir.replace(/^papers\//, "");
}

type RemoteJob = {
  job_id: string;
  status: string;
  paper_dir?: string;
  seeds?: string;
  publish?: boolean;
  created_at?: number;
  started_at?: number;
  ended_at?: number;
  run_id?: string | null;
  exit_code?: number | null;
  error?: string | null;
  preview_url?: string | null;
  log_tail?: string[];
  verdicts?: RunDetail["verdicts"];
  has_report?: boolean;
  degraded?: boolean;
  duration_s?: number;
};

function mapJob(job: RemoteJob): RunDetail {
  const logs = job.log_tail || [];
  const status = job.status;
  return {
    job_id: job.job_id,
    run_id: job.run_id,
    status,
    title: titleForDir(job.paper_dir),
    paper_slug: slugFromDir(job.paper_dir),
    paper_dir: job.paper_dir,
    created_at:
      typeof job.created_at === "number"
        ? new Date(job.created_at * 1000).toISOString()
        : new Date().toISOString(),
    exit_code: job.exit_code ?? null,
    logs,
    stages: stagesFromLogs(logs, status),
    verdicts: job.verdicts || null,
    report: null,
    error: job.error || null,
    preview_url: job.preview_url || null,
    degraded: job.degraded,
    source: "render",
  };
}

export async function fetchHealth(): Promise<boolean> {
  if (isDemoMode()) return true;
  try {
    const res = await fetch("/api/healthz");
    if (!res.ok) return false;
    const data = (await res.json()) as { ok?: boolean };
    return Boolean(data.ok);
  } catch {
    return false;
  }
}

export async function fetchPapers(): Promise<PaperSummary[]> {
  const dirs = isDemoMode()
    ? DEMO_PAPER_DIRS
    : await parse<string[]>(await fetch("/api/papers"));
  return dirs.map((paper_dir) => {
    const slug = paper_dir.replace(/^papers\//, "");
    return {
      slug,
      paper_dir,
      title: PAPER_TITLES[slug] || slug,
      authors: [],
      ready: true,
      chars: 0,
    };
  });
}

export async function fetchRuns(): Promise<RunSummary[]> {
  const jobs = isDemoMode()
    ? (demoStartedAt === null ? [] : [demoJob(Date.now() - demoStartedAt) as unknown as RemoteJob])
    : await parse<RemoteJob[]>(await fetch("/api/runs"));
  return jobs.map((job) => {
    const detail = mapJob(job);
    return {
      job_id: detail.job_id,
      run_id: detail.run_id,
      status: detail.status,
      title: detail.title,
      paper_slug: detail.paper_slug,
      paper_dir: detail.paper_dir,
      created_at: detail.created_at,
      source: detail.source,
    };
  });
}

export async function fetchRun(jobId: string): Promise<RunDetail> {
  if (isDemoMode()) {
    const elapsed = demoStartedAt === null ? DEMO_TOTAL_MS : Date.now() - demoStartedAt;
    const detail = mapJob(demoJob(elapsed) as unknown as RemoteJob);
    if (elapsed >= DEMO_CODE_AT_MS) detail.code = DEMO_CODE;
    if (elapsed >= DEMO_TOTAL_MS) {
      detail.report = DEMO_REPORT;
      detail.repo = DEMO_REPO;
    }
    return detail;
  }
  const job = await parse<RemoteJob>(await fetch(`/api/runs/${encodeURIComponent(jobId)}`));
  const detail = mapJob(job);
  if (job.has_report && job.run_id) {
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(jobId)}/report`);
      if (res.ok) detail.report = await res.text();
    } catch {
      /* report is optional */
    }
  }
  return detail;
}

export function resolvePaperDir(input: {
  message?: string;
  paper_slug?: string | null;
}): string | null {
  if (input.paper_slug) {
    const slug = input.paper_slug.replace(/^papers\//, "");
    return `papers/${slug}`;
  }
  const message = (input.message || "").toLowerCase();
  for (const [alias, dir] of Object.entries(ALIASES)) {
    if (message.includes(alias)) return dir;
  }
  const m = message.match(/papers\/[a-z0-9_-]+/);
  return m ? m[0] : null;
}

export async function startRun(body: {
  message?: string;
  paper_slug?: string;
  paper_text?: string;
  title?: string;
  seeds?: string;
  publish?: boolean;
}): Promise<{ job_id: string; title: string; status: string; paper_slug?: string }> {
  if (isDemoMode()) {
    demoStartedAt = Date.now();
    return {
      job_id: DEMO_JOB_ID,
      status: "queued",
      title: titleForDir("papers/fashion-mnist"),
      paper_slug: "fashion-mnist",
    };
  }
  if (body.paper_text && body.paper_text.trim().length >= 400) {
    throw new Error(
      "Paste-to-run needs the local API. On Render, pick a paper from the sidebar or type its name (e.g. fashion-mnist).",
    );
  }
  const paper_dir = resolvePaperDir(body);
  if (!paper_dir) {
    throw new Error(
      "Name a known paper (fashion-mnist, best-scored-rf) or pick one from the sidebar.",
    );
  }
  const started = await parse<{ job_id: string; status: string; queue_depth?: number }>(
    await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paper_dir,
        seeds: body.seeds || "17,41,93",
        publish: Boolean(body.publish),
      }),
    }),
  );
  return {
    job_id: started.job_id,
    status: started.status,
    title: titleForDir(paper_dir),
    paper_slug: slugFromDir(paper_dir) || undefined,
  };
}

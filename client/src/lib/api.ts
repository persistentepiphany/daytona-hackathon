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
  paper_id: string;
  slug: string;
  title: string;
  paper_dir?: string;
  arxiv_id?: string;
  authors: string[];
  ready: boolean;
  chars: number;
  status: string;
  status_detail?: string | null;
  source?: string;
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
  description?: string;
  source?: "render" | "daytona" | string;
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
  artifacts?: Array<{ artifact_id: string; name: string; kind: string; sha256: string; size: number; url: string }>;
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
  return status === "queued" || status === "running" || status === "publishing";
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
  id?: string;
  at: string;
  stage: string;
  label: string;
  text: string;
  level: "ok" | "error" | "info";
};

type DaytonaStreamEvent = {
  id?: number;
  kind?: string;
  payload?: Record<string, unknown>;
  t?: number;
  stage?: string;
  source?: string;
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

function streamText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function streamTime(timestamp: unknown): string {
  if (typeof timestamp !== "number") return "";
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Converts one redacted SSE frame from a Daytona run into a dashboard feed row. */
export function parseDaytonaEvent(raw: unknown, fallbackId = ""): FeedEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const event = raw as DaytonaStreamEvent;
  const kind = event.kind || "event";
  const payload = event.payload || {};
  const attempt = streamText(payload.attempt_id);
  const experiment = streamText(payload.exp_id) || streamText(payload.experiment_id);
  const suffix = experiment || attempt;
  const state = streamText(payload.state);

  if (kind === "pipeline.log") {
    const source = event.source === "daytona" ? "Daytona" : "Render";
    const content = streamText(payload.text);
    return {
      id: String(event.id ?? fallbackId),
      at: streamTime(event.t),
      stage: event.stage || "PREFLIGHT",
      label: `${source} · ${event.stage || "pipeline"}`,
      text: content.replace(/^\[\d{2}:\d{2}:\d{2}\]\s*/, "").slice(0, 1_200),
      level: /failed|error|NOT ATTEMPTABLE|NOT REPRODUCED/i.test(content) ? "error" : "info",
    };
  }

  let label = "Daytona";
  let text = "Live sandbox event received.";
  let level: FeedEvent["level"] = "info";

  switch (kind) {
    case "agent.action":
      label = "Daytona setup";
      text = `${streamText(payload.role) || "Implementer"}: ${streamText(payload.summary) || "applied an action"}`;
      break;
    case "agent.observation":
      label = "Daytona setup";
      text = streamText(payload.tail) || "Sandbox action completed.";
      level = Number(payload.exit) === 0 ? "ok" : "error";
      break;
    case "log.chunk":
      label = "Daytona output";
      text = payload.suppressed
        ? "Output withheld for a held-out claim."
        : payload.truncated
          ? "Output limit reached; the run continues."
          : streamText(payload.text) || "Sandbox output received.";
      break;
    case "attempt.state":
      label = "Daytona sandbox";
      text = `${suffix ? `${suffix}: ` : ""}${state || "updated"}`;
      level = state === "failed" ? "error" : state === "done" || state === "running" ? "ok" : "info";
      break;
    case "attempt.progress":
      label = "Daytona progress";
      text = `${suffix ? `${suffix}: ` : ""}${payload.done ?? 0}/${payload.total ?? "?"} seeds complete`;
      break;
    case "gate.changed":
      label = streamText(payload.gate) || "Gate";
      text = `${streamText(payload.state) || "updated"} before Daytona execution`;
      break;
    case "verdict.emitted":
      label = "P3 Verdict";
      text = `${experiment ? `${experiment}: ` : ""}${streamText(payload.verdict) || "verdict emitted"}`;
      level = "ok";
      break;
    case "run.done":
      label = "Daytona run";
      text = "Execution stream complete.";
      level = "ok";
      break;
    default:
      if (kind === "intake" || kind === "planner_proposal" || kind === "gate_approved") {
        label = kind === "intake" ? "P0 Intake" : kind === "planner_proposal" ? "Planner" : "G1";
        text = kind === "gate_approved" ? "Plan approved; Daytona work may begin." : "Pipeline event recorded.";
      } else {
        text = `${kind.replace(/[_\.]/g, " ")} recorded${suffix ? ` for ${suffix}` : ""}.`;
      }
  }

  return {
    id: String(event.id ?? fallbackId),
    at: streamTime(event.t),
    stage: kind,
    label,
    text: text.slice(0, 1_200),
    level,
  };
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
  paper_id?: string;
  title?: string;
  stage?: string;
  terminal_classification?: string | null;
  stages?: Record<string, StageState>;
  logs?: string[];
  report?: string | null;
  repo?: RunDetail["repo"];
  github_repo_url?: string | null;
  github_commit_sha?: string | null;
  artifacts?: RunDetail["artifacts"];
};

function mapJob(job: RemoteJob): RunDetail {
  const logs = job.logs || job.log_tail || [];
  const status = job.status;
  return {
    job_id: job.job_id,
    run_id: job.run_id,
    status,
    title: job.title || titleForDir(job.paper_dir),
    paper_slug: job.paper_id || slugFromDir(job.paper_dir),
    paper_dir: job.paper_dir,
    created_at:
      typeof job.created_at === "number"
        ? new Date(job.created_at * 1000).toISOString()
        : new Date().toISOString(),
    exit_code: job.exit_code ?? null,
    logs,
    stages: job.stages || stagesFromLogs(logs, status),
    verdicts: job.verdicts || null,
    error: job.error || null,
    preview_url: job.preview_url || null,
    degraded: job.degraded,
    source: "render",
    report: job.report || null,
    repo: job.repo || null,
    message: job.terminal_classification || undefined,
    artifacts: job.artifacts || [],
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
    : await parse<Array<string | PaperSummary>>(await fetch("/api/papers"));
  return dirs.map((entry) => {
    if (typeof entry !== "string") return entry;
    const paper_dir = entry;
    const slug = paper_dir.replace(/^papers\//, "");
    return {
      paper_id: `bundled-${slug}`,
      slug,
      paper_dir,
      title: PAPER_TITLES[slug] || slug,
      authors: [],
      ready: true,
      chars: 0,
      status: "ready",
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
  if (!detail.report && job.has_report && job.run_id) {
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

function arxivInput(message?: string): string | null {
  const value = message || "";
  const url = value.match(/https?:\/\/(?:www\.)?(?:arxiv\.org|export\.arxiv\.org)\/(?:abs|pdf)\/[^\s]+/i);
  if (url) return url[0].replace(/[),.;]+$/, "");
  const id = value.match(/\b(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?\/\d{7}(?:v\d+)?)\b/i);
  return id?.[0] || null;
}

export async function ingestArxiv(value: string): Promise<PaperSummary> {
  return parse<PaperSummary>(await fetch("/api/papers/arxiv", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arxiv_id_or_url: value }),
  }));
}

export async function fetchPaper(paperId: string): Promise<PaperSummary> {
  return parse<PaperSummary>(await fetch(`/api/papers/${encodeURIComponent(paperId)}`));
}

export async function waitForPaperReady(paperId: string, timeoutMs = 300_000): Promise<PaperSummary> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const paper = await fetchPaper(paperId);
    if (paper.ready) return paper;
    if (paper.status === "failed" || paper.status === "needs_ocr") {
      throw new Error(paper.status_detail || `Paper ingestion ended with ${paper.status}`);
    }
    await new Promise((resolve) => window.setTimeout(resolve, 2_000));
  }
  throw new Error("Paper ingestion is still running; it remains saved and can be opened from the sidebar.");
}

export async function uploadPaper(file: File): Promise<PaperSummary> {
  const created = await parse<{ upload_id: string; paper_id: string; upload_url: string; headers?: Record<string, string> }>(
    await fetch("/api/papers/uploads", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, size: file.size }),
    }),
  );
  const uploaded = await fetch(created.upload_url, { method: "PUT", headers: created.headers, body: file });
  if (!uploaded.ok) throw new Error(`PDF upload failed (HTTP ${uploaded.status})`);
  await parse(await fetch(`/api/papers/uploads/${encodeURIComponent(created.upload_id)}/complete`, { method: "POST" }));
  return waitForPaperReady(created.paper_id);
}

export async function approveG3(jobId: string): Promise<void> {
  await parse(await fetch(`/api/runs/${encodeURIComponent(jobId)}/gates/G3/approve`, {
    method: "POST",
  }));
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
  let paperId = body.paper_slug || null;
  const arxiv = arxivInput(body.message);
  if (!paperId && arxiv) {
    const ingested = await ingestArxiv(arxiv);
    paperId = (ingested.ready ? ingested : await waitForPaperReady(ingested.paper_id)).paper_id;
  }
  if (!paperId) {
    const paperDir = resolvePaperDir(body);
    paperId = paperDir ? `bundled-${paperDir.replace(/^papers\//, "")}` : null;
  }
  if (!paperId) {
    throw new Error(
      "Enter an arXiv ID/URL or pick an ingested paper from the sidebar.",
    );
  }
  const started = await parse<{ job_id: string; status: string; queue_depth?: number }>(
    await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paper_id: paperId,
        seeds: body.seeds || "17,41,93",
        publish: Boolean(body.publish),
      }),
    }),
  );
  return {
    job_id: started.job_id,
    status: started.status,
    title: body.title || paperId,
    paper_slug: paperId,
  };
}

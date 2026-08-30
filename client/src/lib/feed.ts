/**
 * Live run feed client — the streaming half of the run API.
 *
 * `GET /api/runs/<job>/events` replays the run's ledger from a cursor and then follows
 * it, so a dropped connection is recoverable: we remember the last event id and
 * reconnect with `?after=<id>`. EventSource on its own would replay from zero, because
 * the endpoint reads the cursor from the query string rather than `Last-Event-ID`.
 *
 * The server also sends a named `estimate` frame. That one is derived state — it is
 * recomputed from the attempts table on every tick and never appended to the ledger —
 * so it replaces rather than accumulates.
 */

export type FeedFrame = {
  id: number;
  kind: string;
  payload: Record<string, unknown>;
  t: number;
};

export type EstimateFrame = {
  fleet?: {
    low_s?: number | null;
    high_s?: number | null;
    n_samples?: number;
    basis?: string;
  };
  ceiling_s?: number | null;
  completion?: { label?: string; known?: boolean } | null;
  round?: { k: number; cap: number; label: string } | null;
  attempts?: { done?: number; running?: number; pending?: number };
};

export type ActivityRow =
  | { key: string; id: number; t: number; kind: "action"; role: string; text: string }
  | {
      key: string;
      id: number;
      t: number;
      kind: "patch";
      path: string;
      added: number;
      removed: number;
      base: string;
      hunk: string;
    }
  | { key: string; id: number; t: number; kind: "observation"; exit: number; tail: string }
  | { key: string; id: number; t: number; kind: "gate"; gate: string; state: string }
  | {
      key: string;
      id: number;
      t: number;
      kind: "verdict";
      label: string;
      verdict: string;
      heldOut: boolean;
    }
  | { key: string; id: number; t: number; kind: "attempt"; text: string; state: string }
  | { key: string; id: number; t: number; kind: "run"; text: string };

export type AttemptTile = {
  attemptId: string;
  label: string;
  state: string;
  done: number;
  total: number;
  etaS: number | null;
  seeds: number | null;
  ttlMin: number | null;
  exit: number | null;
  verdict: string | null;
  heldOut: boolean;
  log: string[];
};

export type GateState = { gate: string; state: string };

export type BudgetState = {
  kind: string;
  spent: number;
  ceiling: number | null;
  exceeded: boolean;
};

export type FeedState = {
  activity: ActivityRow[];
  attempts: AttemptTile[];
  gates: GateState[];
  budget: BudgetState | null;
  estimate: EstimateFrame | null;
  lastId: number;
  frames: number;
  finished: boolean;
};

/** Connection lifecycle, kept separate from run status: a run can be healthy while the
 *  stream is reconnecting, and queued while the stream is legitimately not open yet. */
export type FeedStatus = "idle" | "waiting" | "live" | "reconnecting" | "finished";

const MAX_ACTIVITY = 300;
const MAX_ATTEMPT_LOG = 40;
const MAX_HUNK_CHARS = 4000;
const MAX_TAIL_CHARS = 600;

export function emptyFeedState(): FeedState {
  return {
    activity: [],
    attempts: [],
    gates: [],
    budget: null,
    estimate: null,
    lastId: 0,
    frames: 0,
    finished: false,
  };
}

export function isFeedEmpty(state: FeedState): boolean {
  return state.frames === 0;
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : value == null ? fallback : String(value);
}

function num(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function maybeNum(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Seconds in the feed's own vocabulary: sub-minute stays in seconds, an hour-plus
 *  loses the minutes. Matches the standalone feed page so the two never disagree. */
export function formatSeconds(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value < 90) return `${Math.round(value)}s`;
  if (value < 5400) return `${Math.round(value / 60)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

export function verdictTone(verdict: string | null | undefined): "ok" | "bad" | "mid" {
  const v = (verdict || "").toUpperCase();
  if (v.includes("WITHIN") || v.includes("PASS")) return "ok";
  if (v.includes("NOT") || v.includes("FAIL")) return "bad";
  return "mid";
}

function tileFor(state: FeedState, attemptId: string): AttemptTile {
  const existing = state.attempts.find((tile) => tile.attemptId === attemptId);
  if (existing) return existing;
  const tile: AttemptTile = {
    attemptId,
    label: attemptId.replace(/^att-/, ""),
    state: "queued",
    done: 0,
    total: 0,
    etaS: null,
    seeds: null,
    ttlMin: null,
    exit: null,
    verdict: null,
    heldOut: false,
    log: [],
  };
  state.attempts.push(tile);
  return tile;
}

function pushActivity(state: FeedState, row: ActivityRow): void {
  state.activity.push(row);
  if (state.activity.length > MAX_ACTIVITY) {
    state.activity.splice(0, state.activity.length - MAX_ACTIVITY);
  }
}

function appendLog(tile: AttemptTile, text: string): void {
  for (const line of text.split("\n")) {
    if (line !== "") tile.log.push(line);
  }
  if (tile.log.length > MAX_ATTEMPT_LOG) {
    tile.log = tile.log.slice(-MAX_ATTEMPT_LOG);
  }
}

/** Fold one ledger event into a draft. Kinds outside the closed vocabulary (the legacy
 *  ledger rows that predate it) are carried in the cursor but not rendered. */
function applyFrame(state: FeedState, frame: FeedFrame): void {
  const p = frame.payload || {};
  const base = { key: `e${frame.id}`, id: frame.id, t: frame.t };

  switch (frame.kind) {
    case "agent.action":
      pushActivity(state, {
        ...base,
        kind: "action",
        role: str(p.role, "agent"),
        text: str(p.summary) || str(p.type, "action"),
      });
      break;

    case "agent.patch":
      pushActivity(state, {
        ...base,
        kind: "patch",
        path: str(p.path, "(file)"),
        added: num(p.added, 0),
        removed: num(p.removed, 0),
        base: str(p.base),
        hunk: str(p.hunk).slice(0, MAX_HUNK_CHARS),
      });
      break;

    case "agent.observation":
      pushActivity(state, {
        ...base,
        kind: "observation",
        exit: num(p.exit, 0),
        tail: str(p.tail).slice(-MAX_TAIL_CHARS),
      });
      break;

    case "log.chunk": {
      const tile = tileFor(state, str(p.attempt_id, "att-unknown"));
      if (p.suppressed) appendLog(tile, `[${num(p.bytes, 0)}B withheld — held-out experiment]`);
      else if (p.truncated) appendLog(tile, "[log truncated at the per-attempt cap]");
      else appendLog(tile, str(p.text));
      break;
    }

    case "attempt.state": {
      const tile = tileFor(state, str(p.attempt_id, "att-unknown"));
      tile.state = str(p.state, tile.state);
      if (p.exp_id) tile.label = str(p.exp_id);
      if (p.seeds != null) tile.seeds = maybeNum(p.seeds);
      if (p.ttl_min != null) tile.ttlMin = maybeNum(p.ttl_min);
      if (p.exit != null) tile.exit = maybeNum(p.exit);
      pushActivity(state, {
        ...base,
        kind: "attempt",
        state: tile.state,
        text: `${tile.label} → ${tile.state}`,
      });
      break;
    }

    case "attempt.progress": {
      const tile = tileFor(state, str(p.attempt_id, "att-unknown"));
      tile.done = num(p.done, tile.done);
      tile.total = num(p.total, tile.total);
      tile.etaS = maybeNum(p.eta_s);
      break;
    }

    case "gate.changed": {
      const gate = str(p.gate);
      const gateState = str(p.state);
      const seen = state.gates.find((entry) => entry.gate === gate);
      if (seen) seen.state = gateState;
      else state.gates.push({ gate, state: gateState });
      pushActivity(state, { ...base, kind: "gate", gate, state: gateState });
      break;
    }

    case "verdict.emitted": {
      const verdict = str(p.verdict);
      const heldOut = Boolean(p.held_out);
      if (p.attempt_id) {
        const tile = tileFor(state, str(p.attempt_id));
        tile.verdict = verdict;
        tile.heldOut = heldOut;
      }
      pushActivity(state, {
        ...base,
        kind: "verdict",
        label: str(p.experiment_id) || str(p.claim_id, "claim"),
        verdict,
        heldOut,
      });
      break;
    }

    case "budget.tick":
      state.budget = {
        kind: str(p.kind, "budget"),
        spent: num(p.spent, 0),
        ceiling: maybeNum(p.ceiling),
        exceeded: str(p.state) === "exceeded",
      };
      break;

    case "run.done":
      state.finished = true;
      pushActivity(state, { ...base, kind: "run", text: "run complete" });
      break;

    default:
      break;
  }
}

/** Fold a batch of frames into a new state. Frames arrive in bursts, so the whole
 *  batch is applied to one draft and published once. */
export function reduceFrames(previous: FeedState, frames: FeedFrame[]): FeedState {
  if (!frames.length) return previous;
  const draft: FeedState = {
    ...previous,
    activity: previous.activity.slice(),
    attempts: previous.attempts.map((tile) => ({ ...tile, log: tile.log.slice() })),
    gates: previous.gates.map((gate) => ({ ...gate })),
  };
  for (const frame of frames) {
    if (frame.id <= draft.lastId) continue; // the cursor handover can repeat an id
    applyFrame(draft, frame);
    draft.lastId = frame.id;
    draft.frames += 1;
  }
  return draft;
}

// ---------------------------------------------------------------------------
// the transport
// ---------------------------------------------------------------------------

export type FeedSubscription = { close: () => void };

export type FeedHandlers = {
  onFrames: (frames: FeedFrame[]) => void;
  onEstimate: (estimate: EstimateFrame) => void;
  onStatus: (status: FeedStatus) => void;
  /** Cursor to resume from, so a remount does not replay a long run from zero. */
  after?: number;
  replay?: "paced" | null;
  speed?: number;
};

const RETRY_BASE_MS = 1500;
const RETRY_MAX_MS = 15000;

export function feedEventsUrl(
  jobId: string,
  options: { after?: number; replay?: "paced" | null; speed?: number } = {},
): string {
  const params = new URLSearchParams();
  if (options.after) params.set("after", String(options.after));
  if (options.replay) {
    params.set("replay", options.replay);
    if (options.speed && options.speed > 1) params.set("speed", String(options.speed));
  }
  const query = params.toString();
  return `/api/runs/${encodeURIComponent(jobId)}/events${query ? `?${query}` : ""}`;
}

/**
 * Follow one job's event stream. Returns a handle whose `close()` is idempotent.
 *
 * The endpoint blocks for up to 30s while a queued job has no run yet and then 409s, so
 * an error before the first frame is "not started", not "broken" — it reports `waiting`
 * and retries with backoff instead of surfacing a failure the user cannot act on.
 */
export function subscribeRunFeed(jobId: string, handlers: FeedHandlers): FeedSubscription {
  let source: EventSource | null = null;
  let timer: number | undefined;
  let closed = false;
  let attempts = 0;
  let cursor = handlers.after || 0;
  let everOpened = false;
  let finished = false;

  const connect = () => {
    if (closed) return;
    handlers.onStatus(everOpened ? "reconnecting" : "waiting");
    const url = feedEventsUrl(jobId, {
      after: cursor,
      // a resumed stream must not re-pace what it already showed
      replay: cursor ? null : handlers.replay,
      speed: handlers.speed,
    });
    const es = new EventSource(url);
    source = es;

    es.onopen = () => {
      if (closed) return;
      attempts = 0;
      everOpened = true;
      handlers.onStatus("live");
    };

    es.onmessage = (event) => {
      if (closed) return;
      let frame: FeedFrame;
      try {
        frame = JSON.parse(event.data) as FeedFrame;
      } catch {
        return; // a half-written frame is not worth tearing the stream down for
      }
      if (typeof frame?.id !== "number") return;
      if (frame.id > cursor) cursor = frame.id;
      everOpened = true;
      handlers.onFrames([frame]);
      if (frame.kind === "run.done") {
        // terminal, but not the last row: teardown records can follow it. Let the open
        // stream drain and let the server end it — just stop chasing it afterwards.
        finished = true;
        handlers.onStatus("finished");
      }
    };

    es.addEventListener("estimate", (event) => {
      if (closed) return;
      try {
        handlers.onEstimate(JSON.parse((event as MessageEvent).data) as EstimateFrame);
      } catch {
        /* an estimate is derived state; dropping one costs nothing */
      }
    });

    es.onerror = () => {
      if (closed) return;
      es.close();
      source = null;
      if (finished) {
        handlers.onStatus("finished");
        return; // the run said it was done; there is nothing to reconnect to
      }
      attempts += 1;
      handlers.onStatus(everOpened ? "reconnecting" : "waiting");
      const delay = Math.min(RETRY_BASE_MS * 2 ** (attempts - 1), RETRY_MAX_MS);
      timer = window.setTimeout(connect, delay);
    };
  };

  connect();

  return {
    close() {
      closed = true;
      if (timer) window.clearTimeout(timer);
      source?.close();
      source = null;
    },
  };
}

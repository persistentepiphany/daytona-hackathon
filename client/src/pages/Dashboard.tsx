import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ChevronDown,
  ChevronRight,
  FileSearch,
  FolderSearch,
  GitBranch,
  Plus,
  Sparkles,
} from "lucide-react";
import {
  fetchHealth,
  fetchPapers,
  fetchRun,
  fetchRuns,
  isActiveStatus,
  startRun,
  type PaperSummary,
  type RunDetail,
  type RunSummary,
  type StageState,
  type VerdictRow,
} from "@/lib/api";

type ActivityProps = {
  icon: React.ReactNode;
  title: string;
  detail: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
};

function Activity({ icon, title, detail, children, defaultOpen = false }: ActivityProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section className="chat-activity">
      <button className="activity-toggle" type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="activity-chevron" aria-hidden="true">
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </span>
        <span className="activity-icon">{icon}</span>
        <span className="activity-label">
          <strong>{title}</strong>
          <small>{detail}</small>
        </span>
      </button>
      {open && <div className="activity-content">{children}</div>}
    </section>
  );
}

const STAGE_META: Record<string, { title: string; icon: React.ReactNode }> = {
  intake: { title: "P0 Intake", icon: <FolderSearch size={15} /> },
  planner: { title: "Planner → claims", icon: <Sparkles size={15} /> },
  freeze: { title: "G1 Freeze prereg", icon: <GitBranch size={15} /> },
  build: { title: "P1 Implementer build", icon: <FileSearch size={15} /> },
  experiments: { title: "P2 Experiments", icon: <GitBranch size={15} /> },
  verdicts: { title: "P3 Verdicts", icon: <Sparkles size={15} /> },
};

function stageDetail(stage: StageState | undefined): string {
  if (!stage) return "pending";
  if (stage.detail) return `${stage.status} · ${stage.detail}`;
  return stage.status;
}

function VerdictTable({ rows }: { rows: VerdictRow[] }) {
  if (!rows.length) return <p className="reasoning-copy">No graded rows yet.</p>;
  return (
    <div className="verdict-table-wrap">
      <table className="verdict-table">
        <thead>
          <tr>
            <th>Experiment</th>
            <th>Claim</th>
            <th>Observed</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.experiment_id}-${row.claim_id}`} data-verdict={row.verdict}>
              <td>{row.experiment_id}</td>
              <td>{row.claim_id}</td>
              <td>{row.observed == null ? "—" : Number(row.observed).toFixed(4)}</td>
              <td>{row.verdict}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type ThreadMessage =
  | { kind: "user"; text: string; at: string }
  | { kind: "assistant"; jobId: string; prompt: string; at: string };

function clock(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function Dashboard() {
  const [message, setMessage] = useState("");
  const [online, setOnline] = useState(false);
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<RunDetail | null>(null);
  const [selectedPaper, setSelectedPaper] = useState<string | null>(null);
  const [thread, setThread] = useState<ThreadMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await fetchHealth();
      if (cancelled) return;
      setOnline(ok);
      if (!ok) {
        setError("API offline — check REPRO_API_ORIGIN / Render service");
        return;
      }
      const [p, r] = await Promise.all([fetchPapers(), fetchRuns()]);
      if (cancelled) return;
      setPapers(p);
      setRuns(r);
    })().catch((err: Error) => {
      if (!cancelled) setError(err.message);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeJobId) return;
    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      try {
        const detail = await fetchRun(activeJobId);
        if (cancelled) return;
        setActiveRun(detail);
        if (isActiveStatus(detail.status)) {
          timer = window.setTimeout(tick, 2500);
        } else {
          const r = await fetchRuns();
          if (!cancelled) setRuns(r);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [activeJobId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [thread, activeRun?.logs.length, activeRun?.status]);

  async function submit(text: string, paperSlug?: string | null) {
    const trimmed = text.trim();
    if (!trimmed && !paperSlug) return;
    setError(null);
    setBusy(true);
    setThread((current) => [
      ...current,
      { kind: "user", text: trimmed || `Reproduce ${paperSlug}`, at: clock() },
    ]);
    setMessage("");
    try {
      const started = await startRun({
        message: trimmed || undefined,
        paper_slug: paperSlug || selectedPaper || undefined,
      });
      setActiveJobId(started.job_id);
      setThread((current) => [
        ...current,
        {
          kind: "assistant",
          jobId: started.job_id,
          prompt: trimmed || started.title,
          at: clock(),
        },
      ]);
      setRuns(await fetchRuns());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function sendMessage(event: React.FormEvent) {
    event.preventDefault();
    void submit(message, selectedPaper);
  }

  function newAnalysis() {
    setActiveJobId(null);
    setActiveRun(null);
    setSelectedPaper(null);
    setThread([]);
    setError(null);
  }

  async function openJob(jobId: string) {
    setError(null);
    setActiveJobId(jobId);
    try {
      const detail = await fetchRun(jobId);
      setActiveRun(detail);
      setThread([
        {
          kind: "user",
          text: `Open job ${detail.title}`,
          at: clock(),
        },
        {
          kind: "assistant",
          jobId,
          prompt: detail.title,
          at: clock(),
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const title = activeRun?.title || selectedPaper || "New analysis";
  const working = activeRun ? isActiveStatus(activeRun.status) : false;

  return (
    <main className="dashboard-page">
      <aside className="dashboard-sidebar">
        <a className="brand dashboard-brand" href="/" aria-label="Snapshot home">
          <img src="/brand/snapshot-mark.svg" width={24} height={24} alt="" />
          <span>SNAPSHOT</span>
        </a>

        <button className="new-chat-button" type="button" onClick={newAnalysis}>
          <Plus size={16} /> New analysis
        </button>

        <div className="sidebar-section">
          <p>Papers</p>
          <nav aria-label="Papers">
            {papers.map((paper) => (
              <button
                key={paper.slug}
                type="button"
                className={`conversation-link ${selectedPaper === paper.slug ? "active" : ""}`}
                onClick={() => {
                  setSelectedPaper(paper.slug);
                  setMessage(`Reproduce ${paper.title}`);
                }}
              >
                {paper.title}
              </button>
            ))}
            {!papers.length && <span className="conversation-link">No papers found</span>}
          </nav>
        </div>

        <div className="sidebar-section">
          <p>Recent jobs</p>
          <nav aria-label="Recent jobs">
            {runs.slice(0, 12).map((run) => (
              <button
                key={run.job_id}
                type="button"
                className={`conversation-link ${activeJobId === run.job_id ? "active" : ""}`}
                onClick={() => void openJob(run.job_id)}
              >
                {run.title}
                <small className="run-status-chip">{run.status}</small>
              </button>
            ))}
            {!runs.length && <span className="conversation-link">No jobs yet</span>}
          </nav>
        </div>

        <div className="sidebar-foot">
          <span className={`status-dot ${online ? "" : "status-dot-off"}`} />
          {online ? "Render API online" : "API offline"}
        </div>
      </aside>

      <section className="dashboard-main" id="current">
        <header className="dashboard-header">
          <div>
            <p className="eyebrow dashboard-eyebrow">Evidence workspace</p>
            <h1>{title}</h1>
          </div>
          {activeJobId && (
            <button
              className="share-button"
              type="button"
              onClick={() => void navigator.clipboard.writeText(activeJobId)}
            >
              Copy job id
            </button>
          )}
        </header>

        <div className="chat-scroll" ref={scrollRef}>
          <div className="chat-thread">
            {!thread.length && (
              <div className="chat-message assistant-message">
                <div className="assistant-heading">
                  <span className="assistant-mark">
                    <Sparkles size={15} />
                  </span>
                  <span>Snapshot</span>
                  <span className="message-meta">ready</span>
                </div>
                <div className="assistant-response">
                  <p>
                    Pick a paper from the sidebar or type <code>reproduce fashion-mnist</code>.
                    Snapshot queues a real Daytona run on the Render API (~7 min). Runs are
                    serialized one at a time.
                  </p>
                </div>
              </div>
            )}

            {thread.map((item, index) => {
              if (item.kind === "user") {
                return (
                  <div className="chat-message user-message" key={`u-${index}`}>
                    <span className="message-meta">You · {item.at}</span>
                    <p>{item.text}</p>
                  </div>
                );
              }

              const detail = activeJobId === item.jobId ? activeRun : null;
              const stages = detail?.stages || {};
              const rows = detail?.verdicts?.verdicts || [];
              const done = detail ? !isActiveStatus(detail.status) : false;

              return (
                <div className="chat-message assistant-message" key={`a-${item.jobId}`}>
                  <div className="assistant-heading">
                    <span className="assistant-mark">
                      <Sparkles size={15} />
                    </span>
                    <span>Snapshot</span>
                    <span className="message-meta">
                      {detail?.status || "starting"} · {item.at}
                    </span>
                  </div>

                  <Activity
                    icon={<Sparkles size={15} />}
                    title="Thinking"
                    detail={working ? "Pipeline running" : done ? "Pipeline finished" : "Queued"}
                    defaultOpen
                  >
                    <p className="reasoning-copy">
                      Job <code>{item.jobId}</code> for “{item.prompt}”: intake → planner → freeze →
                      implementer → experiments → verdicts.
                      {detail?.run_id ? (
                        <>
                          {" "}
                          Artifact run: <code>{detail.run_id}</code>
                        </>
                      ) : null}
                    </p>
                  </Activity>

                  {Object.entries(STAGE_META).map(([key, meta]) => {
                    const stage = stages[key];
                    const status = stage?.status || "pending";
                    return (
                      <Activity
                        key={key}
                        icon={meta.icon}
                        title={meta.title}
                        detail={stageDetail(stage)}
                        defaultOpen={status === "running" || status === "failed"}
                      >
                        <div className={`tool-detail ${status === "running" ? "tool-running" : ""}`}>
                          {status === "running" && <span className="pulse-dot" />}
                          <code>{stage?.detail || status}</code>
                        </div>
                      </Activity>
                    );
                  })}

                  {!!detail?.logs?.length && (
                    <Activity
                      icon={<FileSearch size={15} />}
                      title="Live log"
                      detail={`${detail.logs.length} lines`}
                      defaultOpen={working}
                    >
                      <pre className="run-log">{detail.logs.slice(-40).join("\n")}</pre>
                    </Activity>
                  )}

                  {done && (
                    <div className="assistant-response">
                      {detail?.error && <p className="error-copy">{detail.error}</p>}
                      <p>
                        {rows.length
                          ? "Graded verdicts are ready."
                          : `Job ended with status ${detail?.status} (exit ${detail?.exit_code ?? "—"}).`}
                      </p>
                      <VerdictTable rows={rows} />
                      {detail?.preview_url && (
                        <p className="reasoning-copy">
                          Preview:{" "}
                          <a href={detail.preview_url} target="_blank" rel="noreferrer">
                            {detail.preview_url}
                          </a>
                        </p>
                      )}
                      {detail?.verdicts?.framing && (
                        <p className="reasoning-copy framing-copy">{detail.verdicts.framing}</p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}

            {error && (
              <div className="chat-message assistant-message">
                <p className="error-copy">{error}</p>
              </div>
            )}
          </div>
        </div>

        <form className="chat-composer" onSubmit={sendMessage}>
          <label className="sr-only" htmlFor="chat-input">
            Ask Snapshot
          </label>
          <textarea
            id="chat-input"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder={
              selectedPaper
                ? `Reproduce ${selectedPaper}`
                : "Type: reproduce fashion-mnist — or pick a paper"
            }
            rows={1}
            disabled={busy || working}
          />
          <button
            className="send-button"
            type="submit"
            aria-label="Send message"
            disabled={busy || working || (!message.trim() && !selectedPaper)}
          >
            <ArrowUp size={18} />
          </button>
          <p>
            Hits the Render API (one run at a time, ~7 min). <kbd>Enter</kbd> to send
          </p>
        </form>
      </section>
    </main>
  );
}

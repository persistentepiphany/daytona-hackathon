import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Code,
  ExternalLink,
  FileSearch,
  FileText,
  FolderSearch,
  GitBranch,
  Plus,
  Radio,
  Sparkles,
  Upload,
} from "lucide-react";
import {
  approveG3,
  fetchHealth,
  fetchPapers,
  fetchRun,
  fetchRuns,
  isActiveStatus,
  parseDaytonaEvent,
  parseFeed,
  startRun,
  uploadPaper,
  type FeedEvent,
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
  description?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
};

function Activity({ icon, title, detail, description, children, defaultOpen = false }: ActivityProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section className="chat-activity">
      <button className="activity-toggle" type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="activity-chevron" aria-hidden="true">
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </span>
        <span className="activity-icon">{icon}</span>
        <span className="activity-label">
          <span>
            <strong>{title}</strong>
            {description && <small className="activity-description">{description}</small>}
          </span>
          <small className="activity-detail">{detail}</small>
        </span>
      </button>
      {open && <div className="activity-content">{children}</div>}
    </section>
  );
}

const STAGE_META: Record<string, { title: string; description: string; icon: React.ReactNode }> = {
  INGEST: {
    title: "Ingest paper",
    description: "Fetch the paper and authoritative metadata on the Render control plane.",
    icon: <FolderSearch size={15} />,
  },
  EXTRACT: {
    title: "Extract PDF",
    description: "Validate the PDF, extract text, and record separate provenance hashes.",
    icon: <FileText size={15} />,
  },
  PREFLIGHT: {
    title: "P0 Preflight",
    description: "Classify claims, search for existing code, and resolve data requirements.",
    icon: <Sparkles size={15} />,
  },
  G1: {
    title: "G1 Freeze prereg",
    description: "Validate and automatically lock the experiment plan before compute begins.",
    icon: <GitBranch size={15} />,
  },
  P1: {
    title: "P1 Implementer build",
    description: "Build and smoke-test the frozen environment inside a Daytona sandbox.",
    icon: <FileSearch size={15} />,
  },
  P2: {
    title: "P2 Experiments",
    description: "Run locked experiments, controls, and seed checks inside Daytona.",
    icon: <GitBranch size={15} />,
  },
  P3: {
    title: "P3 Verdicts",
    description: "Grade results against the locked criteria and issue verdicts.",
    icon: <Sparkles size={15} />,
  },
  P4: {
    title: "P4 Adaptive follow-up",
    description: "Run at most one separately approved follow-up without rewriting the preregistration.",
    icon: <GitBranch size={15} />,
  },
  PACKAGE: {
    title: "P5 Evidence package",
    description: "Assemble code, reports, manifests, checksums, and evidence for review.",
    icon: <FileText size={15} />,
  },
  G3: {
    title: "G3 Publish approval",
    description: "Require an explicit human approval before creating or updating GitHub.",
    icon: <GitBranch size={15} />,
  },
  GITHUB_PUBLISH: {
    title: "Private GitHub snapshot",
    description: "Publish the approved evidence commit to a private repository under persistentepiphany.",
    icon: <GitBranch size={15} />,
  },
};

function stageDetail(stage: StageState | undefined): string {
  if (!stage) return "pending";
  if (stage.detail) return `${stage.status} · ${stage.detail}`;
  return stage.status;
}

function snapshotFromLogs(logs: string[]): string | null {
  for (const line of logs) {
    const match = line.match(/\bS0 frozen\s+(s0-[^\s]+)/);
    if (match) return match[1];
  }
  return null;
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

function LiveFeed({ events, running, streaming }: { events: FeedEvent[]; running: boolean; streaming: boolean }) {
  const tailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    tailRef.current?.scrollIntoView({ block: "nearest" });
  }, [events.length]);

  if (!events.length) return null;
  const current = events[events.length - 1];

  return (
    <div className="live-feed">
      <div className="live-feed-head">
        <span className={running ? "pulse-dot" : "status-dot"} />
        <strong>{streaming ? (running ? "Live Render + Daytona feed" : "Persisted execution feed") : "Pipeline log"}</strong>
        <span>{running ? current.text : `${events.length} events`}</span>
      </div>
      <ol className="live-feed-list">
        {events.map((event, index) => {
          const live = running && index === events.length - 1;
          return (
            <li
              className={`feed-event feed-${event.level}${live ? " feed-live" : ""}`}
              key={`${event.at}-${index}`}
            >
              <span className="feed-time">{event.at || "--:--:--"}</span>
              <span className="feed-stage">{event.label}</span>
              <span className="feed-text">{event.text}</span>
            </li>
          );
        })}
      </ol>
      <div ref={tailRef} />
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
  const [daytonaEvents, setDaytonaEvents] = useState<FeedEvent[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
    if (!papers.some((paper) => !paper.ready && !["failed", "needs_ocr"].includes(paper.status))) return;
    const timer = window.setInterval(() => {
      void fetchPapers().then(setPapers).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [papers]);

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
    setDaytonaEvents([]);
  }, [activeJobId]);

  useEffect(() => {
    if (!activeJobId || !activeRun?.run_id || !isActiveStatus(activeRun.status)) return;

    const received = new Set<string>();
    const stream = new EventSource(`/api/runs/${encodeURIComponent(activeJobId)}/events`);
    stream.onmessage = (message) => {
      try {
        const event = parseDaytonaEvent(JSON.parse(message.data), message.lastEventId);
        if (!event || (event.id && received.has(event.id))) return;
        if (event.id) received.add(event.id);
        setDaytonaEvents((current) => [...current, event].slice(-240));
      } catch {
        // A malformed stream frame should not interrupt the running reproduction.
      }
    };
    return () => stream.close();
  }, [activeJobId, activeRun?.run_id, activeRun?.status]);

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

  async function handleUpload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const paper = await uploadPaper(file);
      setPapers(await fetchPapers());
      setSelectedPaper(paper.paper_id);
      await submit(`Reproduce uploaded PDF: ${paper.title}`, paper.paper_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function publishRun(jobId: string) {
    setBusy(true);
    setError(null);
    try {
      await approveG3(jobId);
      setActiveRun(await fetchRun(jobId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
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

  const title = activeRun?.title || papers.find((paper) => paper.paper_id === selectedPaper)?.title || "New analysis";
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
                className={`conversation-link ${selectedPaper === paper.paper_id ? "active" : ""}`}
                onClick={() => {
                  setSelectedPaper(paper.paper_id);
                  setMessage(`Reproduce ${paper.title}`);
                }}
                disabled={!paper.ready}
              >
                {paper.title}
                {!paper.ready && <small className="run-status-chip">{paper.status}</small>}
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
                    Snapshot uses the Render API to queue a Daytona reproduction (~7 min). Runs are
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
              const snapshot = detail ? snapshotFromLogs(detail.logs) : null;

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
                      Job <code>{item.jobId}</code> for “{item.prompt}”: ingest → preflight → G1 →
                      Daytona build → Daytona experiments → verdicts → package → G3.
                      {" "}Render is the durable control plane. P1 environment work and P2 experiments
                      execute inside isolated Daytona sandboxes; the feed labels both locations.
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
                        description={meta.description}
                        defaultOpen={status === "running" || status === "failed"}
                      >
                        <div className={`tool-detail ${status === "running" ? "tool-running" : ""}`}>
                          {status === "running" && <span className="pulse-dot" />}
                          <code>{stage?.detail || status}</code>
                        </div>
                      </Activity>
                    );
                  })}

                  <Activity
                    icon={<GitBranch size={15} />}
                    title="Execution & source"
                    detail={detail?.run_id ? "Daytona-backed" : "Awaiting G1"}
                  >
                    <p className="reasoning-copy">
                      Render coordinates this job. The reproducible environment and experiments run on
                      Daytona; the frozen S0 snapshot is a private Daytona asset, not a public URL.
                    </p>
                    {snapshot && (
                      <p className="reasoning-copy">
                        Frozen Daytona snapshot: <code>{snapshot}</code>
                      </p>
                    )}
                    <p className="reasoning-copy provenance-link">
                      <a href="https://github.com/persistentepiphany/daytona-hackathon" target="_blank" rel="noreferrer">
                        Open the pipeline source on GitHub <ExternalLink size={11} />
                      </a>
                    </p>
                  </Activity>

                  {!!detail?.code?.length && (
                    <Activity
                      icon={<Code size={15} />}
                      title="Generated code (frozen S0)"
                      detail={detail.code.map((f) => f.name).join(", ")}
                      defaultOpen
                    >
                      {detail.code.map((file) => (
                        <div className="code-file" key={file.name}>
                          <a
                            className="code-file-name"
                            href={file.url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {file.name} <ExternalLink size={10} />
                          </a>
                          <pre className="run-log">{file.body}</pre>
                        </div>
                      ))}
                    </Activity>
                  )}

                  {!!(daytonaEvents.length || detail?.logs?.length) && (
                    <Activity
                      icon={<Radio size={15} />}
                      title={daytonaEvents.length ? "Live Daytona feed" : "Pipeline log"}
                      detail={`${daytonaEvents.length || detail?.logs?.length || 0} events`}
                      defaultOpen
                    >
                      <LiveFeed
                        events={daytonaEvents.length ? daytonaEvents : parseFeed(detail?.logs || [])}
                        running={working}
                        streaming={daytonaEvents.length > 0}
                      />
                    </Activity>
                  )}

                  {!!detail?.logs?.length && (
                    <Activity
                      icon={<FileSearch size={15} />}
                      title="Raw log"
                      detail={`${detail.logs.length} lines`}
                    >
                      <pre className="run-log">{detail.logs.slice(-40).join("\n")}</pre>
                    </Activity>
                  )}

                  {!!detail?.artifacts?.length && (
                    <Activity
                      icon={<FileText size={15} />}
                      title="Durable evidence artifacts"
                      detail={`${detail.artifacts.length} private objects`}
                    >
                      <div className="repo-card-files">
                        {detail.artifacts.map((artifact) => (
                          <a key={artifact.artifact_id} href={artifact.url} target="_blank" rel="noreferrer">
                            {artifact.name} · {artifact.sha256.slice(0, 12)} <ExternalLink size={10} />
                          </a>
                        ))}
                      </div>
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
                      {detail?.status === "awaiting_g3" && (
                        <button
                          className="new-chat-button"
                          type="button"
                          disabled={busy}
                          onClick={() => void publishRun(detail.job_id)}
                        >
                          <GitBranch size={14} /> Approve G3 and publish private repository
                        </button>
                      )}
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
                      {detail?.repo && (
                        <section className="repo-card">
                          <a
                            className="repo-card-head"
                            href={detail.repo.url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <GitBranch size={16} />
                            <span>
                              <strong>{detail.repo.name}</strong>
                              <small>
                                {detail.repo.branch} · artifacts committed for {detail.run_id}
                              </small>
                            </span>
                            <ExternalLink size={13} />
                          </a>
                          <div className="repo-card-files">
                            {detail.repo.files.map((file) => (
                              <a
                                key={file.label}
                                href={file.url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {file.label}
                              </a>
                            ))}
                          </div>
                        </section>
                      )}
                      {detail?.report && (
                        <Activity
                          icon={<FileText size={15} />}
                          title="Reproduction report"
                          detail="report.md"
                        >
                          <pre className="run-log">{detail.report}</pre>
                        </Activity>
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
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf,.pdf"
            className="sr-only"
            onChange={(event) => void handleUpload(event.target.files?.[0])}
          />
          <button
            className="upload-button"
            type="button"
            aria-label="Upload PDF"
            title="Upload a PDF directly to private object storage"
            disabled={busy || working}
            onClick={() => fileInputRef.current?.click()}
          >
            <Upload size={17} />
          </button>
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
                : "Enter an arXiv ID/URL, choose a paper, or upload a PDF"
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
            Render persists the job; P1/P2 execute on Daytona. <kbd>Enter</kbd> to send
          </p>
        </form>
      </section>
    </main>
  );
}

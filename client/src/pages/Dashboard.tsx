import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Download,
  FileSearch,
  FolderSearch,
  GitBranch,
  Image as ImageIcon,
  Plus,
  Sparkles,
  Upload,
} from "lucide-react";
import {
  fetchFromArxiv,
  fetchHealth,
  fetchPapers,
  fetchRun,
  fetchRuns,
  figureUrl,
  isActiveStatus,
  isIngestActive,
  pollIngest,
  startRun,
  uploadPaper,
  type FigureScan,
  type IngestJob,
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

function FigureCard({ slug, figure }: { slug: string; figure: FigureScan }) {
  const src = figureUrl(slug, figure.file);
  return (
    <li className="figure-card">
      {src ? (
        <a href={src} target="_blank" rel="noreferrer">
          <img src={src} alt={`${figure.label} from the paper`} loading="lazy" />
        </a>
      ) : (
        <div className="figure-missing">
          <ImageIcon size={15} /> no crop
        </div>
      )}
      <div className="figure-body">
        <p className="figure-label">
          {figure.label} <span>page {figure.page}</span>
        </p>
        {figure.caption && <p className="figure-caption">{figure.caption}</p>}
        {figure.reading ? (
          <p className="figure-reading">
            <strong>Figure scan.</strong> {figure.reading}
          </p>
        ) : (
          <p className="figure-reading figure-reading-off">
            Not read: {figure.error || "no reading"}
          </p>
        )}
      </div>
    </li>
  );
}

function PaperScanCard({
  job,
  busy,
  onRun,
}: {
  job: IngestJob;
  busy: boolean;
  onRun: (paper: PaperSummary) => void;
}) {
  const paper = job.manifest;
  const active = isIngestActive(job);
  const figures = paper?.figures || [];
  const read = figures.filter((f) => f.scanned).length;

  return (
    <>
      <Activity
        icon={<Download size={15} />}
        title={job.kind === "arxiv" ? "arXiv fetch" : "PDF upload"}
        detail={`${job.status} · ${job.label}`}
        defaultOpen={active || job.status === "failed"}
      >
        <div className={`tool-detail ${active ? "tool-running" : ""}`}>
          {active && <span className="pulse-dot" />}
          <code>{job.log.length ? job.log[job.log.length - 1] : "queued"}</code>
        </div>
        {job.log.length > 1 && <pre className="run-log">{job.log.join("\n")}</pre>}
      </Activity>

      {job.status === "failed" && (
        <div className="assistant-response">
          <p className="error-copy">{job.error || "ingest failed"}</p>
        </div>
      )}

      {paper && (
        <>
          <Activity
            icon={<FileSearch size={15} />}
            title="Paper scan"
            detail={`${paper.pages ?? "?"} pages · ${Math.round((paper.chars || 0) / 1000)}k chars · ${figures.length} figures (${read} read)`}
            defaultOpen
          >
            <div className="scan-summary">
              <p className="scan-title">{paper.title}</p>
              {!!paper.authors?.length && (
                <p className="reasoning-copy">{paper.authors.join(", ")}</p>
              )}
              <dl className="scan-facts">
                <div>
                  <dt>Paper dir</dt>
                  <dd>
                    <code>{paper.paper_dir}</code>
                  </dd>
                </div>
                {paper.arxiv_id && (
                  <div>
                    <dt>arXiv</dt>
                    <dd>
                      <a href={paper.abs_url || `https://arxiv.org/abs/${paper.arxiv_id}`} target="_blank" rel="noreferrer">
                        {paper.arxiv_id}
                      </a>
                    </dd>
                  </div>
                )}
                <div>
                  <dt>Code search</dt>
                  <dd>{paper.code_absence || "—"}</dd>
                </div>
              </dl>
              {paper.abstract && <p className="reasoning-copy scan-abstract">{paper.abstract}</p>}
            </div>
          </Activity>

          {!!figures.length && (
            <Activity
              icon={<ImageIcon size={15} />}
              title="Diagrams and tables"
              detail={`${figures.length} extracted · ${read} read by the vision model`}
              defaultOpen={read > 0}
            >
              <ul className="figure-grid">
                {figures.map((figure) => (
                  <FigureCard key={figure.index} slug={paper.slug} figure={figure} />
                ))}
              </ul>
            </Activity>
          )}

          <div className="assistant-response">
            <p>
              The paper is staged at <code>{paper.paper_dir}</code>. Figure readings are folded
              into <code>paper-extract.txt</code>, so the planner sees the diagrams and tables
              alongside the prose.
            </p>
            <button className="run-paper-button" type="button" disabled={busy} onClick={() => onRun(paper)}>
              Run the pipeline on this paper
            </button>
          </div>
        </>
      )}
    </>
  );
}

type ThreadMessage =
  | { kind: "user"; text: string; at: string }
  | { kind: "assistant"; jobId: string; prompt: string; at: string }
  | { kind: "scan"; ingestId: string; job: IngestJob; at: string };

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
  const [addQuery, setAddQuery] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

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

  async function submit(
    text: string,
    paperSlug?: string | null,
    paperDir?: string | null,
    title?: string,
  ) {
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
      const slug = paperSlug || selectedPaper || undefined;
      const started = await startRun({
        message: trimmed || undefined,
        paper_slug: slug,
        // an ingested paper is not in the client's alias table, so its directory
        // is passed through rather than guessed from the message
        paper_dir: paperDir || (slug ? papers.find((p) => p.slug === slug)?.paper_dir : undefined),
        title,
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

  /** Drive one ingest to a terminal state, streaming its log into the thread. */
  async function runIngest(label: string, begin: () => Promise<IngestJob>) {
    setError(null);
    setIngesting(true);
    setThread((current) => [...current, { kind: "user", text: label, at: clock() }]);
    try {
      let job = await begin();
      const at = clock();
      setThread((current) => [
        ...current,
        { kind: "scan", ingestId: job.ingest_id, job, at },
      ]);
      const update = (next: IngestJob) =>
        setThread((current) =>
          current.map((item) =>
            item.kind === "scan" && item.ingestId === next.ingest_id
              ? { ...item, job: next }
              : item,
          ),
        );
      while (isIngestActive(job)) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        job = await pollIngest(job.ingest_id);
        update(job);
      }
      if (job.manifest) {
        setPapers(await fetchPapers());
        setSelectedPaper(job.manifest.slug);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIngesting(false);
    }
  }

  function addFromArxiv() {
    const query = addQuery.trim();
    if (!query || ingesting) return;
    setAddQuery("");
    void runIngest(`Fetch from arXiv: ${query}`, () => fetchFromArxiv(query));
  }

  function addFromFile(file: File | null | undefined) {
    if (!file || ingesting) return;
    if (!/\.pdf$/i.test(file.name)) {
      setError("Upload a PDF — that is what the scanner reads.");
      return;
    }
    void runIngest(`Upload ${file.name}`, () => uploadPaper(file));
  }

  function runPaper(paper: PaperSummary) {
    setSelectedPaper(paper.slug);
    void submit(`Reproduce ${paper.title}`, paper.slug, paper.paper_dir, paper.title);
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
    setAddQuery("");
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

  const selected = papers.find((paper) => paper.slug === selectedPaper);
  const title = activeRun?.title || selected?.title || selectedPaper || "New analysis";
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
          <p>Add a paper</p>
          <div className="add-paper">
            <input
              type="text"
              value={addQuery}
              placeholder="arXiv id, URL or title"
              aria-label="arXiv id, URL or title"
              disabled={ingesting}
              onChange={(event) => setAddQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addFromArxiv();
                }
              }}
            />
            <button type="button" onClick={addFromArxiv} disabled={ingesting || !addQuery.trim()}>
              <Download size={14} /> Fetch from arXiv
            </button>
            <button type="button" onClick={() => fileRef.current?.click()} disabled={ingesting}>
              <Upload size={14} /> Upload a PDF
            </button>
            <input
              ref={fileRef}
              className="sr-only"
              type="file"
              accept="application/pdf,.pdf"
              onChange={(event) => {
                addFromFile(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
            <small>{ingesting ? "Scanning…" : "Text and figures are scanned on arrival."}</small>
          </div>
        </div>

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
                {(paper.source && paper.source !== "committed") || paper.figures?.length ? (
                  <small className="run-status-chip">
                    {[
                      paper.source && paper.source !== "committed" ? paper.source : null,
                      paper.figures?.length ? `${paper.figures.length} figures` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                ) : null}
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

      <section
        className={`dashboard-main ${dragging ? "is-dropping" : ""}`}
        id="current"
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node)) return;
          setDragging(false);
        }}
        onDrop={(event) => {
          if (!event.dataTransfer.files.length) return;
          event.preventDefault();
          setDragging(false);
          addFromFile(event.dataTransfer.files[0]);
        }}
      >
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
                    Pick a paper from the sidebar, pull one in from arXiv, or drop a PDF
                    anywhere on this panel. Snapshot extracts the text, crops every figure and
                    table, reads them with a vision model, then queues a real Daytona run on the
                    Render API (~7 min). Runs are serialized one at a time.
                  </p>
                </div>
              </div>
            )}

            {thread.map((item, index) => {
              if (item.kind === "scan") {
                return (
                  <div className="chat-message assistant-message" key={`s-${item.ingestId}`}>
                    <div className="assistant-heading">
                      <span className="assistant-mark">
                        <Sparkles size={15} />
                      </span>
                      <span>Snapshot</span>
                      <span className="message-meta">
                        {item.job.status} · {item.at}
                      </span>
                    </div>
                    <PaperScanCard job={item.job} busy={busy || working} onRun={runPaper} />
                  </div>
                );
              }
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
                : "Type: reproduce fashion-mnist — or add a paper from arXiv or a PDF"
            }
            rows={1}
            disabled={busy || working || ingesting}
          />
          <button
            className="send-button"
            type="submit"
            aria-label="Send message"
            disabled={busy || working || ingesting || (!message.trim() && !selectedPaper)}
          >
            <ArrowUp size={18} />
          </button>
          <p>
            Hits the Render API (one run at a time, ~7 min). <kbd>Enter</kbd> to send · drop a
            PDF anywhere to scan it
          </p>
        </form>
      </section>
    </main>
  );
}

import { useState } from "react";
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
      <button className="activity-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
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

export default function Dashboard() {
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<string[]>([]);

  function sendMessage(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed) return;
    setMessages((current) => [...current, trimmed]);
    setMessage("");
  }

  return (
    <main className="dashboard-page">
      <aside className="dashboard-sidebar">
        <a className="brand dashboard-brand" href="/" aria-label="Snapshot home">
          <img src="/brand/snapshot-mark.svg" width={24} height={24} alt="" />
          <span>SNAPSHOT</span>
        </a>

        <button className="new-chat-button" type="button">
          <Plus size={16} /> New analysis
        </button>

        <div className="sidebar-section">
          <p>Recent analyses</p>
          <nav aria-label="Recent analyses">
            <a className="conversation-link active" href="#current">Reproduce Fashion-MNIST results</a>
            <a className="conversation-link" href="#older">GPT-2 scaling-law review</a>
            <a className="conversation-link" href="#older">CIFAR-10 benchmark audit</a>
          </nav>
        </div>

        <div className="sidebar-foot">
          <span className="status-dot" /> Evidence engine online
        </div>
      </aside>

      <section className="dashboard-main" id="current">
        <header className="dashboard-header">
          <div>
            <p className="eyebrow dashboard-eyebrow">Evidence workspace</p>
            <h1>Reproduce Fashion-MNIST results</h1>
          </div>
          <button className="share-button" type="button">Share</button>
        </header>

        <div className="chat-scroll">
          <div className="chat-thread">
            <div className="chat-message user-message">
              <span className="message-meta">You · 10:42</span>
              <p>Can you check whether the paper’s claimed 93.7% test accuracy reproduces from the supplied repository?</p>
            </div>

            <div className="chat-message assistant-message">
              <div className="assistant-heading">
                <span className="assistant-mark"><Sparkles size={15} /></span>
                <span>Snapshot</span>
                <span className="message-meta">working</span>
              </div>

              <Activity
                icon={<Sparkles size={15} />}
                title="Thinking"
                detail="Evidence plan prepared"
                defaultOpen
              >
                <p className="reasoning-copy">
                  I’ll locate the reported metric, inspect the training configuration, then run the documented evaluation path against the frozen environment.
                </p>
              </Activity>

              <Activity icon={<FolderSearch size={15} />} title="Tool call" detail="repository.search · completed">
                <div className="tool-detail">
                  <code>repository.search({'{ query: "93.7 test accuracy" }'})</code>
                  <p>Found a matching claim in <code>paper.md:84</code> and evaluation settings in <code>config/default.yaml</code>.</p>
                </div>
              </Activity>

              <Activity icon={<GitBranch size={15} />} title="Tool call" detail="environment.freeze · completed">
                <div className="tool-detail">
                  <code>environment.freeze({'{ python: "3.11", cuda: "12.1" }'})</code>
                  <p>Locked dependencies from <code>requirements.txt</code>; no version conflicts found.</p>
                </div>
              </Activity>

              <Activity icon={<FileSearch size={15} />} title="Tool call" detail="run_evaluation · running">
                <div className="tool-detail tool-running">
                  <span className="pulse-dot" /> Running <code>python evaluate.py --seed 42</code>
                </div>
              </Activity>

              <div className="assistant-response">
                <p>I’ve verified the claim and prepared the exact evaluation environment. The reproduction run is in progress; I’ll attach the verdict and full artifact trail when it completes.</p>
              </div>
            </div>

            {messages.map((item, index) => (
              <div className="chat-message user-message new-message" key={`${item}-${index}`}>
                <span className="message-meta">You · now</span>
                <p>{item}</p>
              </div>
            ))}
          </div>
        </div>

        <form className="chat-composer" onSubmit={sendMessage}>
          <label className="sr-only" htmlFor="chat-input">Ask Snapshot</label>
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
            placeholder="Ask about this reproduction…"
            rows={1}
          />
          <button className="send-button" type="submit" aria-label="Send message" disabled={!message.trim()}>
            <ArrowUp size={18} />
          </button>
          <p>Snapshot reports evidence summaries and tool activity. <kbd>Enter</kbd> to send</p>
        </form>
      </section>
    </main>
  );
}

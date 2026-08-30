/**
 * The run's activity, in ledger order: what an agent did, what it wrote, what came
 * back, and every gate and verdict as it lands. Pinned to the newest row unless the
 * reader is hovering, which is the only reliable signal that they are reading rather
 * than watching.
 */
import { useEffect, useRef, useState } from "react";
import { verdictTone, type ActivityRow } from "@/lib/feed";

function PatchBody({ hunk }: { hunk: string }) {
  return (
    <pre className="feed-patch-body">
      {hunk.split("\n").map((line, index) => {
        const header = line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@");
        const tone = header
          ? "hunk"
          : line.startsWith("+")
            ? "added"
            : line.startsWith("-")
              ? "removed"
              : "";
        return (
          <b className={tone} key={index}>
            {line || " "}
          </b>
        );
      })}
    </pre>
  );
}

function Row({ row }: { row: ActivityRow }) {
  switch (row.kind) {
    case "patch":
      return (
        <article className="feed-patch">
          <h4>
            <span className="feed-patch-path">{row.path}</span>
            <span className="feed-patch-added">+{row.added}</span>
            <span className="feed-patch-removed">−{row.removed}</span>
            <span className="feed-patch-base">
              {row.base === "empty" ? "new file (or first write this run)" : "diff vs this run"}
            </span>
          </h4>
          <PatchBody hunk={row.hunk} />
        </article>
      );

    case "observation":
      return (
        <div className={`feed-row feed-row-observation ${row.exit ? "feed-row-fail" : ""}`}>
          <span className="feed-row-role">exit {row.exit}</span>
          <span className="feed-row-text feed-row-wrap">{row.tail}</span>
        </div>
      );

    case "gate":
      return (
        <div className="feed-row">
          <span className="feed-row-role">gate</span>
          <span className="feed-row-text">
            {row.gate} <em data-gate={row.state}>{row.state}</em>
          </span>
        </div>
      );

    case "verdict":
      return (
        <div className="feed-row">
          <span className="feed-row-role">verdict</span>
          <span className="feed-row-text">
            {row.label}:{" "}
            <em data-tone={verdictTone(row.verdict)}>{row.verdict}</em>
            {row.heldOut ? <small> (held-out)</small> : null}
          </span>
        </div>
      );

    case "attempt":
      return (
        <div className="feed-row">
          <span className="feed-row-role">attempt</span>
          <span className="feed-row-text">{row.text}</span>
        </div>
      );

    case "run":
      return (
        <div className="feed-row">
          <span className="feed-row-role">run</span>
          <span className="feed-row-text">{row.text}</span>
        </div>
      );

    default:
      return (
        <div className="feed-row">
          <span className="feed-row-role">{row.role}</span>
          <span className="feed-row-text">{row.text}</span>
        </div>
      );
  }
}

export default function ActivityStream({ rows }: { rows: ActivityRow[] }) {
  const box = useRef<HTMLDivElement>(null);
  const [held, setHeld] = useState(false);

  useEffect(() => {
    if (held || !box.current) return;
    box.current.scrollTop = box.current.scrollHeight;
  }, [rows, held]);

  if (!rows.length) {
    return <p className="reasoning-copy">Connected — waiting for the first event.</p>;
  }

  return (
    <div
      className="feed-stream"
      ref={box}
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
    >
      {rows.map((row) => (
        <Row row={row} key={row.key} />
      ))}
    </div>
  );
}

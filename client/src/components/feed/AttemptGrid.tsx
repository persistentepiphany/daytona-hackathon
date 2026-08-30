/**
 * One tile per attempt, created lazily by the first event that names it. The bar is the
 * attempt's own seed progress and the ETA beside it is measured from that attempt's
 * observed seed rate — never a prior, and never borrowed from another attempt.
 */
import { useEffect, useRef, useState } from "react";
import { formatSeconds, verdictTone, type AttemptTile } from "@/lib/feed";

function AttemptLog({ lines }: { lines: string[] }) {
  const box = useRef<HTMLPreElement>(null);
  const [held, setHeld] = useState(false);

  useEffect(() => {
    if (held || !box.current) return;
    box.current.scrollTop = box.current.scrollHeight;
  }, [lines, held]);

  if (!lines.length) return null;
  return (
    <pre
      className="feed-tile-log"
      ref={box}
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
    >
      {lines.join("\n")}
    </pre>
  );
}

function meta(tile: AttemptTile): string {
  const parts: string[] = [];
  if (tile.total) parts.push(`${tile.done}/${tile.total} seeds`);
  else if (tile.seeds) parts.push(`${tile.seeds} seeds`);
  if (tile.etaS != null) parts.push(`measured ETA ${formatSeconds(tile.etaS)}`);
  else if (tile.ttlMin != null) parts.push(`TTL ${tile.ttlMin}m`);
  if (tile.exit != null) parts.push(`exit ${tile.exit}`);
  return parts.join(" · ");
}

export default function AttemptGrid({ tiles }: { tiles: AttemptTile[] }) {
  if (!tiles.length) {
    return <p className="reasoning-copy">No attempts have been spawned yet.</p>;
  }

  return (
    <div className="feed-grid">
      {tiles.map((tile) => {
        const pct = tile.total ? Math.round((100 * tile.done) / tile.total) : 0;
        const detail = meta(tile);
        return (
          <article className={`feed-tile feed-tile-${tile.state}`} key={tile.attemptId}>
            <h4>
              <span className="feed-tile-name">{tile.label}</span>
              <span className="feed-tile-state">{tile.state}</span>
              {tile.verdict && (
                <span className="feed-tile-verdict" data-tone={verdictTone(tile.verdict)}>
                  {tile.verdict}
                  {tile.heldOut ? " · held-out" : ""}
                </span>
              )}
            </h4>
            <div
              className="feed-bar feed-bar-measured"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pct}
              aria-label={`${tile.label} seed progress`}
            >
              <i style={{ width: `${pct}%` }} />
            </div>
            {detail && <p className="feed-tile-meta">{detail}</p>}
            <AttemptLog lines={tile.log} />
          </article>
        );
      })}
    </div>
  );
}

/**
 * The timing strip: gates, budget, and two bars that are deliberately not the same
 * quantity — the solid one is measured progress across attempts, the hollow one is the
 * enforced upper bound (remaining TTLs, capped by budget). They are labelled in full so
 * neither can be read as the other, and no whole-run estimate is shown while a build
 * round is still pending.
 */
import { formatSeconds, type BudgetState, type EstimateFrame, type GateState } from "@/lib/feed";

type Props = {
  gates: GateState[];
  budget: BudgetState | null;
  estimate: EstimateFrame | null;
};

function measuredLabel(estimate: EstimateFrame | null, done: number, total: number): string {
  if (!estimate) return "measured: waiting for a completed attempt";
  if (estimate.round) {
    return `${estimate.round.label} — no whole-run estimate while a round is pending`;
  }
  const fleet = estimate.fleet || {};
  if (fleet.basis === "config_default") {
    return `${done}/${total} attempts · no measurements yet — ceiling only`;
  }
  return (
    `${done}/${total} attempts · fleet ${formatSeconds(fleet.low_s)}–${formatSeconds(fleet.high_s)}` +
    ` (queue simulation over a measured median, n=${fleet.n_samples ?? 0}, ${fleet.basis ?? "measured"})`
  );
}

export default function RunTimeline({ gates, budget, estimate }: Props) {
  const attempts = estimate?.attempts || {};
  const done = attempts.done || 0;
  const total = done + (attempts.running || 0) + (attempts.pending || 0);
  const pct = total ? Math.round((100 * done) / total) : 0;

  return (
    <div className="feed-strip">
      {(gates.length > 0 || budget) && (
        <div className="feed-strip-chips">
          {gates.map((gate) => (
            <span className="feed-chip" data-gate={gate.state} key={gate.gate}>
              {gate.gate} {gate.state}
            </span>
          ))}
          {budget && (
            <span className="feed-chip" data-budget={budget.exceeded ? "exceeded" : "ok"}>
              {budget.kind} {Math.round(budget.spent)}
              {budget.ceiling != null ? ` / ${Math.round(budget.ceiling)}` : ""}
              {budget.exceeded ? " — ceiling reached" : ""}
            </span>
          )}
        </div>
      )}

      <div className="feed-bars">
        <div
          className="feed-bar feed-bar-measured"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          aria-label="Attempts completed"
        >
          <i style={{ width: `${pct}%` }} />
        </div>
        <p className="feed-bar-label">{measuredLabel(estimate, done, total)}</p>
        <div className="feed-bar feed-bar-ceiling" aria-hidden="true">
          <i />
        </div>
        <p className="feed-bar-label">
          ceiling {formatSeconds(estimate?.ceiling_s)} — enforced upper bound (remaining TTLs,
          capped by budget)
        </p>
      </div>
    </div>
  );
}

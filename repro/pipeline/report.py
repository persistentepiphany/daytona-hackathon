"""Verdict report: controls first, then the target paper's graded verdicts.

Every row cites its prereg rule, attempt ids, and evidence hash, so any number in
the table can be traced to a manifest and re-executed from the ledger. Primary
(preregistered) results are separated from adaptive rounds. The framing rule
appears verbatim.
"""

import json

from ..orchestrator.ledger import Ledger

FRAMING = ("failure to reproduce is evidence the paper as written is insufficient "
           "to reconstruct the result - not evidence the authors are wrong")


def generate_report(run_id: str, prereg: dict, rows: list[dict], sham_rows: list[dict],
                    hermeticity: str, ledger: Ledger, paper_title: str,
                    code_absence: dict | None = None,
                    adaptive_rows: list[dict] | None = None,
                    calibration_note: str | None = None) -> str:
    run = ledger.run(run_id)
    evidence_by_attempt = {a["attempt_id"]: a["evidence_sha"]
                           for a in ledger.attempts_for(run_id)}
    lines = [
        f"# Reproduction report: {paper_title}",
        "",
        f"*{FRAMING}*",
        "",
        "## Run lineage",
        "",
        f"1. Run id: `{run_id}`",
        f"2. Preregistration hash: `{run['prereg_hash']}`",
        f"3. Frozen snapshot S0: `{run['s0_snapshot']}` (recipe `{run['recipe_sha']}`, git `{run['s0_git_sha']}`)",
        f"4. Paper hash: `{run['paper_hash']}`",
        "",
        "## Controls (scored before the target rows)",
        "",
    ]
    if calibration_note:
        lines += [f"1. Calibration: {calibration_note}"]
    else:
        lines += ["1. Calibration: this run is itself the calibration paper run."]
    lines += [f"2. Hermeticity: {hermeticity}", ""]
    lines += _table("Sham twin (corrupted targets; expected NOT REPRODUCED)",
                    sham_rows, evidence_by_attempt)
    lines += _table("Primary preregistered results", rows, evidence_by_attempt)
    if adaptive_rows:
        lines += _table("ADAPTIVE round (cannot alter primary verdicts)",
                        adaptive_rows, evidence_by_attempt)
    if code_absence:
        lines += ["## Code-absence certification", "",
                  f"1. Status: {code_absence.get('status')}",
                  f"2. Queries: {', '.join(code_absence.get('queries', []))}"]
        for i, r in enumerate(code_absence.get("results", []), start=3):
            lines.append(f"{i}. {r.get('title')} - {r.get('url')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _table(title: str, rows: list[dict], evidence_by_attempt: dict) -> list[str]:
    out = [f"## {title}", "",
           "| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict | Rule | Attempts | Evidence |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        attempts = r.get("attempt_ids", [])
        ev = ", ".join(filter(None, ((evidence_by_attempt.get(a) or "")[:12] for a in attempts)))
        out.append(
            f"| {r['experiment_id']} | {r['claim_id']} | {r['type']} | "
            f"{'yes' if r.get('held_out') else 'no'} | {r['observed']} | {r['delta']} | "
            f"**{r['verdict']}** | {r['rule_id']} | {', '.join(a[:12] for a in attempts)} | {ev} |"
        )
    out.append("")
    return out


def report_from_files(run_dir, ledger: Ledger, paper_title: str) -> str:
    """Rebuild the report purely from persisted artifacts (no in-memory state)."""
    from pathlib import Path

    run_dir = Path(run_dir)
    verdicts = json.loads((run_dir / "verdicts.json").read_text())
    prereg = json.loads((run_dir / "prereg.json").read_text())
    return generate_report(
        verdicts["run_id"], prereg, verdicts["verdicts"], verdicts["sham"],
        verdicts["hermeticity"], ledger, paper_title,
        code_absence=verdicts.get("code_absence"),
        adaptive_rows=verdicts.get("adaptive"),
    )

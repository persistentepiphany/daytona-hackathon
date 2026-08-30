# Demo — exact commands

Two paths exist. The **calibration path** is proven and is what you should demo
unless the autonomous status below says otherwise. The **autonomous path** is the
one with no hand-written experiment code in it.

## 0. Prerequisites

```bash
pip install -r requirements.txt
```

Environment: `DAYTONA_API_KEY` (or `DAYTONA_API`), `ZAI_API_KEY` (or `ZAI_API`)
for the LLM roles, `PARALLEL_API_KEY` (or `PARALLEL_API`) for search. Any unset
name is read from a `.env` file in the repo root.

## 1. Calibration path — proven, ~4m30s

Grades a paper against a hand-written reference implementation. This is the path
that proves the *grader* is honest: it carries a sham twin whose target is
deliberately corrupted, and a hermeticity control that runs with all networking
blocked.

```bash
python scripts/run_e2e.py                 # full run: intake -> S0 -> experiments -> verdicts -> preview
python scripts/publish_results.py         # redeploy the preview and print its URL
```

Latest committed run: `results/e2e/e2e-1788097734/` — E001 reproduced within
tolerance, E004 not reproduced, sham twin correctly refused, hermeticity
verified.

## 2. Autonomous path — no hand-written code in the path

The Planner writes the preregistration from the paper text; the Implementer
writes `train.py`, `config.json` and `smoke.sh` and builds the environment.
Nothing about the paper is hard-coded.

```bash
python scripts/auto_run.py                          # defaults to papers/fashion-mnist
python scripts/auto_run.py papers/<slug> --seeds 17,41,93
```

Artifacts land in `runs/auto/<run_id>/`: `prereg.json` (model-written contract),
`build.json` (every implementer round, its files and why it failed),
`verdicts.json`, `report.md`.

Exit codes: `0` graded verdicts produced · `2` smoke gate never passed within
4 rounds (no S₀, no experiments) · `3` S₀ built but nothing graded.

## 3. Dashboard and tests

```bash
repro dashboard --ledger runs/e2e/ledger.db --evidence-root runs/e2e   # 127.0.0.1:8600
python -m pytest -q                                                    # 47 passed, 2 skipped
```

## 4. Status of the autonomous path

**Not green — demo the calibration path (section 1).**

The autonomous path builds a model-written contract and a model-written S₀
reliably (3 of 3 attempts reached a passing smoke gate, one of them recovering
from two failed rounds), but no run has produced graded verdicts yet. Two
defects are open; both are written up in PROGRESS.md under "Autonomous driver".
The branch `auto-driver` is pushed and deliberately unmerged.

Live preview URL (from the calibration path, run `e2e-1788097734`):
`https://8000-886e29d5-2053-45f4-806a-cba62608aca1.daytonaproxy01.eu`

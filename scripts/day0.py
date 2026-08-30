"""Day-0 account verification with a pass/fail report. Run locally against a live
account (requires DAYTONA_API_KEY; PARALLEL_API_KEY for item 9):

    python scripts/day0.py [--skip-gpu] [--skip-parallel]

Wraps the probe suite in scripts/day0_check.py (checklist items 1-9: snapshot API,
VM/fork availability, freeze-and-boot, network_block_all, volumes, resize,
concurrency, GPU, org/tier, credit stacking, Parallel round-trip) and reduces each
result to PASS / FAIL / MANUAL. Exit code 0 when nothing failed.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from day0_check import Day0  # noqa: E402

# results that report an account limitation are findings, not failures
EXPECTED_SUBSTRINGS = ("NOT AVAILABLE", "REJECTED", "SKIPPED", "MANUAL")


def grade(value: str) -> str:
    if value.startswith("FAIL"):
        return "FAIL"
    if any(s in value for s in ("MANUAL", "SKIPPED")):
        return "MANUAL"
    return "PASS"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-gpu", action="store_true")
    ap.add_argument("--skip-parallel", action="store_true")
    args = ap.parse_args()

    day0 = Day0(skip_gpu=args.skip_gpu, skip_parallel=args.skip_parallel)
    day0.run()

    print("\n## Pass/fail report\n")
    failed = 0
    for item in sorted(day0.results):
        value = day0.results[item]
        verdict = grade(value)
        if verdict == "FAIL":
            failed += 1
        print(f"[{verdict}] {item}: {value}")
    print(f"\n{len(day0.results)} checks, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

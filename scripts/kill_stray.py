"""Delete leftover sandboxes, keeping the build sandbox that serves the preview.

A run tears down its own sandboxes; this catches what a crashed or interrupted
run left behind. Sandboxes labeled kind=build are preserved by default because
they host the deployed preview for its demo window.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repro.orchestrator.daytona_client import make_daytona  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-kind", default="build,build_demo",
                    help="comma-separated 'kind' labels to preserve "
                         "(default: build,build_demo - P5 labels the preview build_demo)")
    ap.add_argument("--keep-run", default="",
                    help="comma-separated run ids to preserve (use for a run still in flight)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    keep_kinds = {k.strip() for k in args.keep_kind.split(",") if k.strip()}
    keep_runs = {r.strip() for r in args.keep_run.split(",") if r.strip()}
    daytona = make_daytona()
    kept, killed = [], []
    for sb in daytona.list():
        labels = getattr(sb, "labels", None) or {}
        if labels.get("kind") in keep_kinds or labels.get("run") in keep_runs:
            kept.append((sb.id, labels))
            continue
        if args.dry_run:
            killed.append((sb.id, labels, "dry-run"))
            continue
        try:
            sb.delete()
            killed.append((sb.id, labels, "deleted"))
        except Exception as e:  # noqa: BLE001 - report and continue
            killed.append((sb.id, labels, f"error: {str(e)[:120]}"))

    for sid, labels in kept:
        print(f"kept    {sid[:12]} {labels}")
    for sid, labels, how in killed:
        print(f"{how:<8}{sid[:12]} {labels}")
    print(f"\n{len(kept)} kept, {len(killed)} targeted")
    return 0


if __name__ == "__main__":
    sys.exit(main())

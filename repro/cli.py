"""Command-line entry point for the reproduction pipeline."""

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    print("repro: pipeline commands land here as stages are built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

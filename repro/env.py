"""Read config: process environment first, then a `.env` file as backup."""

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def env_key(*names: str) -> str | None:
    """Return the first non-empty value among `names`.

    Process environment wins. If none of the names are set (or they are empty),
    the same names are read from a `.env` file in the current directory or the
    repository root.
    """
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    parsed = _parse_dotenv(_find_dotenv())
    for n in names:
        v = parsed.get(n)
        if v:
            return v
    return None


def _find_dotenv() -> Path | None:
    for path in (Path.cwd() / ".env", _REPO_ROOT / ".env"):
        if path.is_file():
            return path
    return None


def _parse_dotenv(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out

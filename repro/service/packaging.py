from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
FORBIDDEN_NAMES = {".env", "credentials", "id_rsa", "id_ed25519", "paper.pdf"}
ALLOWED_SUFFIXES = {".json", ".md", ".txt", ".py", ".sh", ".toml", ".lock", ".yaml", ".yml"}
SECRET_RE = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY)"
)


class UnsafeArtifact(RuntimeError):
    pass


def collect_run_artifacts(run_dir: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.name.lower() in FORBIDDEN_NAMES or path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        if path.stat().st_size > MAX_ARTIFACT_BYTES:
            continue
        data = path.read_bytes()
        if SECRET_RE.search(data):
            raise UnsafeArtifact(f"secret-like value detected in {rel}")
        files[str(rel)] = data
    manifest = {name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                for name, data in files.items()}
    files["artifact-manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode()
    return files


def github_snapshot(files: dict[str, bytes], run_id: str) -> dict[str, bytes]:
    """Place immutable run output under runs/<id>; never include source PDFs or datasets."""
    return {f"runs/{run_id}/{name}": data for name, data in files.items()}

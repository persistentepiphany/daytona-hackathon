"""Data staging: one networked container downloads datasets into the shared volume,
records a sha256 per file in the ledger, and dies. Experiments later re-verify those
checksums before compute — the volume has no read-only mount, so integrity is enforced
by verification, not mount flags.
"""

from ..orchestrator.adapter import SandboxAdapter
from ..orchestrator.ledger import Ledger
from ..orchestrator.lifecycle import Lifecycle

MOUNT = "/data"


class StagingError(RuntimeError):
    pass


def stage_datasets(lifecycle: Lifecycle, adapter: SandboxAdapter, ledger: Ledger,
                   run_id: str, base_snapshot: str, volume_name: str,
                   files: dict[str, str], subdir: str) -> dict[str, str]:
    """files: {relative_name: url}. Returns {volume_path: sha256}."""
    volume_id = adapter.volume_ensure(volume_name)
    sid = lifecycle.create("data_stager", name=f"stage-{run_id}", snapshot=base_snapshot,
                           volumes=[(volume_id, MOUNT)])
    hashes: dict[str, str] = {}
    try:
        r = adapter.exec(sid, f"mkdir -p {MOUNT}/{subdir}")
        if r.exit_code != 0:
            raise StagingError(f"mkdir failed: {r.output}")
        for name, url in files.items():
            dest = f"{MOUNT}/{subdir}/{name}"
            r = adapter.exec(sid, f"curl -sSL --max-time 600 -o '{dest}' '{url}' && sync", timeout=900)
            if r.exit_code != 0:
                raise StagingError(f"download failed for {url}: {r.output[-500:]}")
            r = adapter.exec(sid, f"sha256sum '{dest}'")
            if r.exit_code != 0:
                raise StagingError(f"checksum failed for {dest}: {r.output[-500:]}")
            sha = r.output.strip().split()[0]
            path = f"{subdir}/{name}"
            hashes[path] = sha
            ledger.record_dataset(run_id, path, sha)
            ledger.log_event(run_id, "dataset_staged", {"path": path, "url": url, "sha256": sha})
    finally:
        lifecycle.stop(sid)  # auto-delete 0 removes it
    return hashes


def checksum_verify_cmd(subdir: str, hashes: dict[str, str]) -> str:
    """Shell command an experiment runs before compute to re-verify dataset integrity."""
    lines = [f"{sha}  {MOUNT}/{path}" for path, sha in hashes.items() if path.startswith(subdir)]
    manifest = "\\n".join(lines)
    return f'printf "{manifest}\\n" | sha256sum -c --strict -'

"""Data staging: the control plane downloads and validates datasets, then uploads
them into a Daytona-mounted volume through the SDK. Experiments later re-verify
those checksums before compute — Daytona never needs arbitrary outbound data access.
"""

from ..orchestrator.adapter import SandboxAdapter
from ..orchestrator.ledger import Ledger
from ..orchestrator.lifecycle import Lifecycle
from ..service.data_staging import fetch_dataset

MOUNT = "/data"


class StagingError(RuntimeError):
    pass


def stage_datasets(lifecycle: Lifecycle, adapter: SandboxAdapter, ledger: Ledger,
                   run_id: str, base_snapshot: str, volume_name: str,
                   files: dict[str, str], subdir: str,
                   data_mode: str = "staged") -> dict[str, str]:
    """files: {relative_name: url}. Returns {volume_path: sha256}.

    With data_mode="synthetic" staging is a no-op: experiments generate their data
    from the manifest's condition, so there is nothing to download or checksum.
    """
    if data_mode == "synthetic":
        ledger.log_event(run_id, "staging_skipped", {"data_mode": "synthetic"})
        return {}
    volume_id = adapter.volume_ensure(volume_name)
    sid = lifecycle.create_with_retry("data_stager", name=f"stage-{run_id}",
                                      snapshot=base_snapshot, volumes=[(volume_id, MOUNT)])
    hashes: dict[str, str] = {}
    try:
        r = adapter.exec(sid, f"mkdir -p {MOUNT}/{subdir}")
        if r.exit_code != 0:
            raise StagingError(f"mkdir failed: {r.output}")
        for name, url in files.items():
            dest = f"{MOUNT}/{subdir}/{name}"
            try:
                data, control_sha = fetch_dataset(url, filename=name)
            except Exception as exc:
                raise StagingError(f"control-plane download failed for {url}: {exc}") from exc
            adapter.write_file(sid, dest, data)
            r = adapter.exec(sid, "sync", timeout=120)
            if r.exit_code != 0:
                raise StagingError(f"volume sync failed for {name}: {r.output[-500:]}")
            r = adapter.exec(sid, f"sha256sum '{dest}'")
            if r.exit_code != 0:
                raise StagingError(f"checksum failed for {dest}: {r.output[-500:]}")
            sha = r.output.strip().split()[0]
            if sha != control_sha:
                raise StagingError(f"checksum changed while uploading {name} to Daytona")
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

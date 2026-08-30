"""P2 executor: one sandbox per scientific question, created directly from S0.

Contract per experiment: validate the manifest against the frozen prereg, boot from
S0, re-verify dataset checksums, upload the manifest + runner, execute with the
seeds looping inside the sandbox, pull the evidence files, then stop (auto-delete
removes the sandbox). Evidence lands both locally (for the verdict stage) and in
the evidence volume when one is mounted.
"""

import hashlib
import json
from pathlib import Path

from ..orchestrator.adapter import SandboxAdapter
from ..orchestrator.ledger import Ledger
from ..orchestrator.lifecycle import Lifecycle
from ..orchestrator.manifest import dump_manifest, validate_manifest
from ..orchestrator.prereg import canonical_json
from .runner_files import LEAKCHECK_PY, RUNNER_PY

WORK = "/home/daytona/work"
EVIDENCE_FILES = ("manifest.json", "metrics.json", "stdout.log", "leakage.json")


class ExperimentError(RuntimeError):
    pass


def run_experiment(life: Lifecycle, adapter: SandboxAdapter, ledger: Ledger, run_id: str,
                   prereg: dict, prereg_hash: str, manifest: dict, s0_snapshot: str,
                   dataset_hashes: dict[str, str], evidence_root: str | Path,
                   volumes: list[tuple[str, str]] | None = None,
                   hermetic: bool = False, data_local_dir: str = "localdata") -> dict:
    mh = validate_manifest(manifest, prereg, prereg_hash)
    exp_id = manifest["experiment_id"]
    ttl = int(manifest["budget"]["ttl_min"]) * 2  # TTL backstop = estimate x 2
    attempt_id = ledger.start_attempt(
        run_id, exp_id, mh, "snapshot", s0_snapshot, manifest["command"],
        manifest["seeds"], claim_id=manifest["claim_id"], cost_est=ttl,
    )
    env_vars = {"PIP_NO_INDEX": "1", "NO_NETWORK": "1"} if hermetic else {}
    sid = life.create(
        "experiment", name=f"{exp_id.lower()}-{run_id}"[:48], snapshot=s0_snapshot,
        exp_id=exp_id, ttl_minutes=ttl, volumes=volumes,
        network_block_all=hermetic, env_vars=env_vars,
    )
    ledger.bind_sandbox(attempt_id, sid)
    evidence_dir = Path(evidence_root) / exp_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 1
    try:
        _verify_datasets(adapter, sid, dataset_hashes, data_local_dir)
        adapter.write_file(sid, f"{WORK}/manifest.json", dump_manifest(manifest).encode())
        adapter.write_file(sid, f"{WORK}/runner.py", RUNNER_PY.encode())
        adapter.write_file(sid, f"{WORK}/runner.sh",
                           b'#!/bin/bash\nexec venv/bin/python runner.py "$@"\n')
        adapter.write_file(sid, f"{WORK}/leakcheck.py", LEAKCHECK_PY.encode())
        r = adapter.exec(sid, f"{manifest['command']} > stdout.log 2>&1", cwd=WORK,
                         env=env_vars or None, timeout=int(manifest["budget"]["ttl_min"]) * 60)
        exit_code = r.exit_code

        checksums = {}
        for name in EVIDENCE_FILES:
            try:
                data = adapter.read_file(sid, f"{WORK}/{name}")
            except FileNotFoundError:
                if name in manifest["expected_outputs"]:
                    raise ExperimentError(f"{exp_id}: expected output {name} missing") from None
                continue
            (evidence_dir / name).write_bytes(data)
            checksums[name] = hashlib.sha256(data).hexdigest()
        (evidence_dir / "checksums.json").write_text(canonical_json(checksums))
        evidence_sha = hashlib.sha256(canonical_json(checksums).encode()).hexdigest()

        if exit_code != 0:
            tail = (evidence_dir / "stdout.log").read_text()[-2000:] if (evidence_dir / "stdout.log").exists() else ""
            raise ExperimentError(f"{exp_id}: runner exited {exit_code}\n{tail}")
        metrics = json.loads((evidence_dir / "metrics.json").read_text())
        ledger.log_event(run_id, "experiment_done", {
            "experiment_id": exp_id, "attempt_id": attempt_id,
            "mean_value": metrics["mean_value"], "evidence_sha": evidence_sha,
            "hermetic": hermetic,
        })
        return metrics
    finally:
        try:
            ledger.finish_attempt(attempt_id, exit_code,
                                  evidence_sha if "evidence_sha" in locals() else None)
        except Exception:
            pass
        life.stop(sid)  # auto-delete interval 0 removes it


def _verify_datasets(adapter: SandboxAdapter, sid: str, dataset_hashes: dict[str, str],
                     data_local_dir: str) -> None:
    """Re-verify staged data against the ledger checksums before any compute."""
    if not dataset_hashes:
        raise ExperimentError("no dataset hashes recorded; refusing to run")
    lines = []
    for path, sha in sorted(dataset_hashes.items()):
        fname = path.split("/")[-1]
        lines.append(f"{sha}  {data_local_dir}/{fname}")
    manifest_text = "\\n".join(lines)
    r = adapter.exec(sid, f'printf "{manifest_text}\\n" | sha256sum -c --strict --quiet -',
                     cwd=WORK, timeout=300)
    if r.exit_code != 0:
        raise ExperimentError(f"dataset checksum verification failed:\n{r.output[-1000:]}")

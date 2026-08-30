"""P2 executor: one sandbox per scientific question, created directly from S0.

Contract per experiment: validate the manifest against the frozen prereg, boot from
S0, re-verify dataset checksums, upload the manifest + runner, execute with the
seeds looping inside the sandbox, pull the evidence files, then stop (auto-delete
removes the sandbox). Evidence lands both locally (for the verdict stage) and in
the evidence volume when one is mounted.
"""

import hashlib
import io
import json
import tarfile
import time
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


def candidate_tarball(files: dict[str, str]) -> tuple[bytes, str]:
    """Deterministic tar.gz of the candidate (sorted names, zeroed metadata) and
    its sha256 — the pinned SHA the delivery is verified against."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
        for name in sorted(files):
            data = files[name].encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def deliver_candidate(adapter: SandboxAdapter, sid: str, files: dict[str, str],
                      work: str = WORK) -> str:
    """Code delivery is a tarball at a pinned SHA, uploaded via the filesystem API
    and verified after landing — never a clone. Returns the pinned SHA."""
    data, sha = candidate_tarball(files)
    remote = f"{work}/candidate-{sha[:12]}.tar.gz"
    adapter.write_file(sid, remote, data)
    landed = adapter.read_file(sid, remote)
    if hashlib.sha256(landed).hexdigest() != sha:
        raise ExperimentError(f"candidate tarball corrupted in transit (expected {sha[:16]})")
    r = adapter.exec(sid, f"tar -xzf {remote}", cwd=work, timeout=300)
    if r.exit_code != 0:
        raise ExperimentError(f"candidate tarball extraction failed: {r.output[-500:]}")
    return sha


def run_experiment(life: Lifecycle, adapter: SandboxAdapter, ledger: Ledger, run_id: str,
                   prereg: dict, prereg_hash: str, manifest: dict, s0_snapshot: str,
                   dataset_hashes: dict[str, str], evidence_root: str | Path,
                   volumes: list[tuple[str, str]] | None = None,
                   hermetic: bool = False, data_local_dir: str = "localdata",
                   candidate_files: dict[str, str] | None = None,
                   data_mode: str = "staged") -> dict:
    mh = validate_manifest(manifest, prereg, prereg_hash)
    exp_id = manifest["experiment_id"]
    # persist the full manifest so a rerun reconstructs from the ledger alone
    ledger.log_event(run_id, "manifest_frozen",
                     {"manifest_hash": mh, "manifest": manifest, "data_mode": data_mode})
    ttl = int(manifest["budget"]["ttl_min"]) * 2  # TTL backstop = estimate x 2
    attempt_id = ledger.start_attempt(
        run_id, exp_id, mh, "snapshot", s0_snapshot, manifest["command"],
        manifest["seeds"], claim_id=manifest["claim_id"], cost_est=ttl,
    )
    env_vars = {"PIP_NO_INDEX": "1", "NO_NETWORK": "1"} if hermetic else {}
    # the org memory quota caps concurrent sandboxes; when slots are held by long
    # training runs, a create must wait patiently for one to free up
    sid = None
    for attempt in range(20):
        try:
            sid = life.create(
                "experiment", name=f"{exp_id.lower()}-{run_id}"[:48], snapshot=s0_snapshot,
                exp_id=exp_id, ttl_minutes=ttl, volumes=volumes,
                network_block_all=hermetic, env_vars=env_vars,
            )
            break
        except Exception as e:
            ledger.log_event(run_id, "sandbox_create_retry",
                             {"exp_id": exp_id, "attempt": attempt + 1, "error": str(e)[:300]})
            if attempt == 19 or "limit" not in str(e).lower():
                ledger.finish_attempt(attempt_id, 1, None)
                raise
            time.sleep(60)
    ledger.bind_sandbox(attempt_id, sid)
    evidence_dir = Path(evidence_root) / exp_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 1
    try:
        if data_mode == "synthetic":
            # no staged data: experiments generate from the manifest's condition
            ledger.log_event(run_id, "synthetic_data", {"exp_id": exp_id,
                                                        "condition": manifest.get("condition")})
        else:
            _verify_datasets(adapter, sid, dataset_hashes, data_local_dir)
        if candidate_files:
            sha = deliver_candidate(adapter, sid, candidate_files)
            ledger.log_event(run_id, "candidate_delivered", {"exp_id": exp_id, "sha256": sha})
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


def reconstruct_attempt(ledger: Ledger, attempt_id: str) -> dict:
    """Rebuild everything a rerun needs from the ledger alone: the replay tuple
    plus the frozen manifest persisted at execution time."""
    replay = ledger.resolve_replay(attempt_id)
    for row in ledger.events_for(replay["run_id"], "manifest_frozen"):
        payload = json.loads(row["payload"])
        if payload.get("manifest_hash") == replay["manifest_hash"]:
            replay["manifest"] = payload["manifest"]
            replay["data_mode"] = payload.get("data_mode", "staged")
            return replay
    raise ExperimentError(f"no frozen manifest recorded for attempt {attempt_id}")


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

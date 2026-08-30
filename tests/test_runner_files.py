"""The files the executor uploads into a sandbox are generated source, not imported.

Nothing in the offline suite executes them, so a syntax error in `RUNNER_PY` reaches the
sandbox intact and every experiment fails there with a missing metrics.json - which is
exactly how a stray escape in the progress line was found, on live Daytona, after S0 had
already been built. These tests make that a one-second failure instead.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repro.pipeline.runner_files import LEAKCHECK_PY, RUNNER_PY


@pytest.mark.parametrize("name,source", [("runner.py", RUNNER_PY),
                                         ("leakcheck.py", LEAKCHECK_PY)])
def test_generated_sources_compile(name, source):
    compile(source, name, "exec")


def lay_out(root: Path, marker: bool):
    """A minimal work dir: a stub interpreter standing in for the S0 venv, so the seed
    loop runs without numpy, sklearn or any data."""
    (root / "localdata").mkdir(parents=True, exist_ok=True)
    (root / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    (root / "runner.py").write_text(RUNNER_PY)
    (root / "manifest.json").write_text(json.dumps(
        {"experiment_id": "E001", "claim_id": "C1", "type": "reproduce",
         "seeds": [17, 41, 93]}))
    (root / "config.json").write_text(json.dumps({"data": {"dir": str(root / "localdata")}}))
    stub = root / "venv" / "bin" / "python"
    stub.write_text('#!/bin/sh\n'
                    'echo \'{"claim":"C1","seed":1,"metric":"test_accuracy",'
                    '"value":0.81}\'\n')
    os.chmod(stub, 0o755)
    (root / "train.py").write_text("")
    (root / "leakcheck.py").write_text("")
    if marker:
        (root / ".repro_progress").write_text("progress.jsonl\n")


def run(root: Path):
    return subprocess.run([sys.executable, "runner.py"], cwd=root,
                          capture_output=True, text=True)


def test_the_runner_completes_and_writes_metrics(tmp_path):
    lay_out(tmp_path, marker=False)
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr[-1500:]
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["n_seeds"] == 3 and metrics["experiment_id"] == "E001"


def test_progress_is_written_only_when_the_marker_asks_for_it(tmp_path):
    off, on = tmp_path / "off", tmp_path / "on"
    lay_out(off, marker=False)
    lay_out(on, marker=True)
    assert run(off).returncode == 0
    assert run(on).returncode == 0

    assert not (off / "progress.jsonl").exists()
    assert (on / "progress.jsonl").read_text().split() == [
        "::progress", "1/3", "::progress", "2/3", "::progress", "3/3"]


def test_stdout_is_byte_identical_with_and_without_the_feed(tmp_path):
    """stdout.log is an evidence file whose bytes are hashed into the attempt record.
    Progress goes to its own channel precisely so this holds."""
    off, on = tmp_path / "off", tmp_path / "on"
    lay_out(off, marker=False)
    lay_out(on, marker=True)
    a, b = run(off), run(on)
    assert a.returncode == b.returncode == 0
    assert a.stdout == b.stdout
    assert "::progress" not in a.stdout and "::progress" not in b.stdout
    assert (off / "metrics.json").read_bytes() == (on / "metrics.json").read_bytes()

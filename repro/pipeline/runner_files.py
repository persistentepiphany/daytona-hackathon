"""Files the executor uploads into every experiment sandbox at run time.

The environment (deps, loader, training code, config) is frozen in S0; these two
files are the execution contract: runner.py walks the manifest (seeds loop inside
the sandbox, one sandbox per scientific question), leakcheck.py is the ride-along
integrity check. Both are hashed into the evidence record.
"""

RUNNER_PY = '''"""Execute one experiment manifest: iterate seeds, aggregate, write evidence."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

WORK = Path(__file__).resolve().parent

# The live feed asks for progress by dropping a marker file naming a side channel.
# Progress is never printed: stdout.log is an evidence file whose bytes are hashed, and
# it must be identical whether or not anyone is watching.
_MARKER = WORK / ".repro_progress"
PROGRESS = (WORK / _MARKER.read_text().strip()) if _MARKER.exists() else None


def _progress(done, total):
    if PROGRESS is None:
        return
    try:
        with open(PROGRESS, "a") as f:
            f.write(f"::progress {done}/{total}\\n")
    except OSError:
        pass  # a watcher's convenience must never interrupt an experiment


def main():
    manifest = json.loads((WORK / "manifest.json").read_text())
    config = json.loads((WORK / "config.json").read_text())
    exp_id = manifest["experiment_id"]

    data_dir = config["data"]["dir"]
    local = WORK / "localdata"
    if not local.exists():
        # stage the volume to local disk before compute; never train against the mount
        shutil.copytree(data_dir, local)
    overrides = [f"data.dir={local}"]

    mutation = manifest.get("mutation")
    if mutation:
        overrides.append(f"{mutation['config_key']}={json.dumps(mutation['value'])}")

    rows = []
    for done, seed in enumerate(manifest["seeds"], start=1):
        cmd = [str(WORK / "venv/bin/python"), str(WORK / "train.py"),
               "--claim", manifest["claim_id"], "--seed", str(seed)]
        for expr in overrides:
            cmd += ["--set", expr]
        print(f"[runner] {exp_id} seed={seed}", flush=True)
        out = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True)
        sys.stdout.write(out.stdout)
        sys.stderr.write(out.stderr)
        if out.returncode != 0:
            raise SystemExit(f"train.py failed for seed {seed}: rc={out.returncode}")
        rows.append(json.loads(out.stdout.strip().splitlines()[-1]))
        _progress(done, len(manifest["seeds"]))

    values = [r["value"] for r in rows]
    metrics = {
        "experiment_id": exp_id,
        "claim_id": manifest["claim_id"],
        "type": manifest["type"],
        "metric": rows[0]["metric"],
        "rows": rows,
        "mean_value": round(sum(values) / len(values), 6),
        "min_value": min(values),
        "max_value": max(values),
        "n_seeds": len(values),
    }
    (WORK / "metrics.json").write_text(json.dumps(metrics, indent=2))

    leak = subprocess.run([str(WORK / "venv/bin/python"), str(WORK / "leakcheck.py"),
                           str(local)], cwd=WORK, capture_output=True, text=True)
    sys.stdout.write(leak.stdout)
    sys.stderr.write(leak.stderr)
    if leak.returncode != 0:
        raise SystemExit(f"leakcheck failed: rc={leak.returncode}")
    print(f"[runner] {exp_id} done mean={metrics['mean_value']}", flush=True)


if __name__ == "__main__":
    main()
'''

LEAKCHECK_PY = '''"""Ride-along integrity check: train/test row-hash overlap on the staged data."""

import hashlib
import json
import sys

try:  # the calibration module ships `fashion`; autonomous builds ship `dataio`
    from dataio import load_split
except ImportError:
    from fashion import load_split

data_dir = sys.argv[1]
X_train, _ = load_split(data_dir, "train")
X_test, _ = load_split(data_dir, "test")
train_hashes = {hashlib.md5(row.tobytes()).hexdigest() for row in X_train}
test_hashes = {hashlib.md5(row.tobytes()).hexdigest() for row in X_test}
overlap = len(train_hashes & test_hashes)
report = {
    "train_test_overlap_rows": overlap,
    "n_train": int(len(X_train)),
    "n_test": int(len(X_test)),
}
with open("leakage.json", "w") as f:
    json.dump(report, f, indent=2)
print(f"[leakcheck] overlap={overlap}")
'''

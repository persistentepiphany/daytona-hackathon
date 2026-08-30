"""Hand-written environment recipe and candidate code for the calibration paper.

This module is the deterministic stand-in for the Implementer role: it drives the
P1 archaeology session with a known-good recipe so the executor loop is proven
before any generated code enters the system. The in-sandbox sources live here as
string constants so they are versioned with the orchestrator and hashed into the
recipe.
"""

import json
from pathlib import Path

DATA_SUBDIR = "fashion-mnist"

PAPER_DIR = Path(__file__).resolve().parents[2] / "papers" / "fashion-mnist"

TOLERANCES = {"C1": 0.01, "C2": 0.01, "C3": 0.02, "C4": 0.01, "C5": 0.04, "C7": 0.01}

CHANCE_ACCURACY = 0.1  # ten balanced classes


def prereg_inputs() -> tuple[dict, list[dict], list[dict], dict[str, float], list[int]]:
    """(paper, claims, experiments, tolerances, seeds) for build_prereg."""
    paper = json.loads((PAPER_DIR / "paper.json").read_text())
    claims = json.loads((PAPER_DIR / "claims.json").read_text())["claims"]
    experiments = []
    for i, claim in enumerate(claims, start=1):
        exp_id = f"E{i:03d}"
        experiments.append({
            "experiment_id": exp_id,
            "claim_id": claim["id"],
            "type": "reproduce",
            "command": f"bash runner.sh {exp_id}",
            "rule": {"id": f"R-{exp_id}", "kind": "abs_tolerance",
                     "target": claim["reported_value"],
                     "tolerance": TOLERANCES[claim["id"]], "aggregate": "mean"},
        })
    experiments.append({
        "experiment_id": "E101",
        "claim_id": "C2",
        "type": "ablation",
        "command": "bash runner.sh E101",
        "mutation": {"config_key": "models.C2.params.n_estimators", "value": 10},
        "rule": {"id": "R-E101", "kind": "direction", "reference_experiment": "E002",
                 "direction": "decrease", "min_delta": 0.003},
    })
    experiments.append({
        "experiment_id": "E102",
        "claim_id": "C1",
        "type": "randomized_control",
        "command": "bash runner.sh E102",
        "mutation": {"config_key": "data.shuffle_labels", "value": True},
        "rule": {"id": "R-E102", "kind": "abs_tolerance", "target": CHANCE_ACCURACY,
                 "tolerance": 0.02, "aggregate": "mean"},
    })
    return paper, claims, experiments, dict(TOLERANCES), list(SEEDS)

DATA_FILES = {
    "train-images-idx3-ubyte.gz": "https://raw.githubusercontent.com/zalandoresearch/fashion-mnist/master/data/fashion/train-images-idx3-ubyte.gz",
    "train-labels-idx1-ubyte.gz": "https://raw.githubusercontent.com/zalandoresearch/fashion-mnist/master/data/fashion/train-labels-idx1-ubyte.gz",
    "t10k-images-idx3-ubyte.gz": "https://raw.githubusercontent.com/zalandoresearch/fashion-mnist/master/data/fashion/t10k-images-idx3-ubyte.gz",
    "t10k-labels-idx1-ubyte.gz": "https://raw.githubusercontent.com/zalandoresearch/fashion-mnist/master/data/fashion/t10k-labels-idx1-ubyte.gz",
}

SEEDS = [17, 41, 93, 127, 251]

LOADER_PY = '''"""Fashion-MNIST idx loader."""

import gzip

import numpy as np


def load_split(data_dir, split):
    prefix = "train" if split == "train" else "t10k"
    with gzip.open(f"{data_dir}/{prefix}-images-idx3-ubyte.gz") as f:
        X = np.frombuffer(f.read(), dtype=np.uint8, offset=16).reshape(-1, 784)
    with gzip.open(f"{data_dir}/{prefix}-labels-idx1-ubyte.gz") as f:
        y = np.frombuffer(f.read(), dtype=np.uint8, offset=8)
    if X.shape[0] != y.shape[0]:
        raise ValueError(f"images/labels mismatch: {X.shape[0]} vs {y.shape[0]}")
    return X, y
'''

CONFIG_JSON = '''{
  "data": {"dir": "localdata", "scale": 255.0, "shuffle_labels": false},
  "models": {
    "C1": {"cls": "DecisionTreeClassifier", "params": {"criterion": "entropy", "max_depth": 10, "splitter": "best"}},
    "C2": {"cls": "RandomForestClassifier", "params": {"n_estimators": 100, "criterion": "entropy", "max_depth": 100, "n_jobs": -1}},
    "C3": {"cls": "LogisticRegression", "params": {"C": 1.0, "penalty": "l2", "max_iter": 1000}},
    "C4": {"cls": "GaussianNB", "params": {"priors": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]}},
    "C5": {"cls": "Perceptron", "params": {"penalty": "l1"}},
    "C7": {"cls": "DecisionTreeClassifier", "params": {"criterion": "entropy", "max_depth": 50, "splitter": "best"}}
  }
}
'''

TRAIN_PY = '''"""Train one configuration for one seed and print a metrics JSON line.

Usage: python train.py --claim C1 --seed 17 [--config config.json]
       [--set models.C2.params.n_estimators=10] [--limit-train N]
The paper protocol: shuffle the training data per repetition, fit, report test
accuracy. Mutations arrive only as --set config diffs, keeping every counterfactual
a machine-checkable config change.
"""

import argparse
import json
import time

import numpy as np

from fashion import load_split

CLASSES = {}


def _register():
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression, Perceptron
    from sklearn.naive_bayes import GaussianNB
    from sklearn.neural_network import MLPClassifier
    from sklearn.tree import DecisionTreeClassifier

    for cls in (RandomForestClassifier, LogisticRegression, Perceptron, GaussianNB,
                MLPClassifier, DecisionTreeClassifier):
        CLASSES[cls.__name__] = cls


def apply_set(config, expr):
    key, raw = expr.split("=", 1)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    node = config
    parts = key.split(".")
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value


def main():
    _register()
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--set", action="append", default=[], dest="sets")
    ap.add_argument("--limit-train", type=int, default=None)
    args = ap.parse_args()

    with open(args.config) as f:
        config = json.load(f)
    for expr in args.sets:
        apply_set(config, expr)

    spec = config["models"][args.claim]
    data = config["data"]
    X_train, y_train = load_split(data["dir"], "train")
    X_test, y_test = load_split(data["dir"], "test")
    scale = float(data.get("scale") or 1.0)
    X_train = X_train.astype(np.float32) / scale
    X_test = X_test.astype(np.float32) / scale

    rng = np.random.default_rng(args.seed)
    perm = rng.permutation(len(X_train))
    X_train, y_train = X_train[perm], y_train[perm]
    if data.get("shuffle_labels"):
        y_train = y_train[rng.permutation(len(y_train))]
    if args.limit_train:
        X_train, y_train = X_train[: args.limit_train], y_train[: args.limit_train]

    cls = CLASSES[spec["cls"]]
    params = dict(spec["params"])
    if "random_state" in cls().get_params():
        params.setdefault("random_state", args.seed)
    model = cls(**params)
    t0 = time.time()
    model.fit(X_train, y_train)
    acc = float((model.predict(X_test) == y_test).mean())
    print(json.dumps({
        "claim": args.claim, "seed": args.seed, "metric": "test_accuracy",
        "value": round(acc, 6), "train_seconds": round(time.time() - t0, 1),
        "n_train": int(len(X_train)), "n_test": int(len(X_test)),
        "config_overrides": args.sets,
    }))


if __name__ == "__main__":
    main()
'''

SMOKE_CHECK_PY = '''"""Smoke gate body: imports resolve, the loader instantiates, one fit+predict completes."""

import json

import numpy as np
import sklearn
from sklearn.tree import DecisionTreeClassifier

from fashion import load_split

with open("config.json") as f:
    config = json.load(f)
X, y = load_split(config["data"]["dir"], "test")
assert X.shape == (10000, 784) and y.shape == (10000,), (X.shape, y.shape)
model = DecisionTreeClassifier(max_depth=3, random_state=0)
model.fit(X[:256] / 255.0, y[:256])
pred = model.predict(X[256:266] / 255.0)
assert pred.shape == (10,)
print(f"SMOKE OK numpy={np.__version__} sklearn={sklearn.__version__}")
'''

SMOKE_SH = '''set -e
cd "$(dirname "$0")"
venv/bin/python smoke_check.py
'''

SOURCE_FILES = {
    "fashion.py": LOADER_PY,
    "config.json": CONFIG_JSON,
    "train.py": TRAIN_PY,
    "smoke_check.py": SMOKE_CHECK_PY,
    "smoke.sh": SMOKE_SH,
}


def build_environment(session) -> None:
    """Drive a P1 ArchaeologySession to a smoke-passing state.

    The staged data volume is copied to local disk and baked into S0, so
    experiments (including hermetic ones with all networking blocked) never
    touch the mount at run time; integrity is re-verified per run against the
    ledger checksums.
    """
    session.sh("python3 -m venv venv", timeout=600)
    session.sh("venv/bin/pip install -q --no-cache-dir numpy scikit-learn", timeout=1800)
    session.sh("venv/bin/pip freeze > requirements.lock")
    session.sh(f"cp -r /data/{DATA_SUBDIR} localdata && ls localdata")
    for name, content in SOURCE_FILES.items():
        session.put_file(name, content)

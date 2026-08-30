#!/bin/bash
# environment recipe, replayed top to bottom
set -e
cat > config.json <<'RECIPE_EOF'
{
  "data": {
    "dir": "localdata"
  },
  "dt_fashion_1": {
    "model": "DecisionTreeClassifier",
    "params": {
      "criterion": "entropy",
      "max_depth": 10,
      "splitter": "best"
    },
    "repetitions": 5
  },
  "svc_fashion_1": {
    "model": "SVC",
    "params": {
      "C": 10,
      "kernel": "poly"
    },
    "repetitions": 5
  }
}
RECIPE_EOF
cat > dataio.py <<'RECIPE_EOF'
import os
import numpy as np
import gzip


def _read_images(path):
    with gzip.open(path, 'rb') as f:
        data = np.frombuffer(f.read(), np.uint8, offset=16)
    return data.reshape(-1, 28 * 28).astype(np.float32) / 255.0


def _read_labels(path):
    with gzip.open(path, 'rb') as f:
        data = np.frombuffer(f.read(), np.uint8, offset=8)
    return data.astype(np.int64)


def load_split(data_dir, split):
    if split == "train":
        X = _read_images(os.path.join(data_dir, "train-images-idx3-ubyte.gz"))
        y = _read_labels(os.path.join(data_dir, "train-labels-idx1-ubyte.gz"))
    elif split == "test":
        X = _read_images(os.path.join(data_dir, "t10k-images-idx3-ubyte.gz"))
        y = _read_labels(os.path.join(data_dir, "t10k-labels-idx1-ubyte.gz"))
    else:
        raise ValueError(f"Unknown split: {split}")
    return X, y
RECIPE_EOF
cat > train.py <<'RECIPE_EOF'
import argparse
import json
import time
import numpy as np
from sklearn import clone
from sklearn.tree import DecisionTreeClassifier
from sklearn.svm import SVC
from sklearn.metrics import accuracy_score

import dataio


def parse_overrides(args):
    overrides = {}
    for s in args.set:
        if '=' in s:
            k, v = s.split('=', 1)
            overrides[k] = v
    return overrides


def apply_config(base_cfg, overrides):
    cfg = json.loads(json.dumps(base_cfg))
    for path, value in overrides.items():
        parts = path.split('.')
        d = cfg
        for p in parts[:-1]:
            if p not in d:
                d[p] = {}
            d = d[p]
        d[parts[-1]] = value
    return cfg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claim', type=str, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--set', type=str, action='append', default=[])
    args = parser.parse_args()

    with open('config.json', 'r') as f:
        base_cfg = json.load(f)
    
    overrides = parse_overrides(args)
    cfg = apply_config(base_cfg, overrides)
    claim_cfg = cfg[args.claim]
    
    data_dir = cfg['data']['dir']
    X_train_full, y_train_full = dataio.load_split(data_dir, 'train')
    X_test, y_test = dataio.load_split(data_dir, 'test')

    model_name = claim_cfg['model']
    params = claim_cfg['params']
    n_reps = claim_cfg.get('repetitions', 1)

    rng = np.random.default_rng(args.seed)
    scores = []
    
    start_time = time.time()
    
    for i in range(n_reps):
        # Shuffle data as per paper: "The data shuffling job is therefore left to the algorithm developer."
        indices = rng.permutation(len(X_train_full))
        X_train = X_train_full[indices]
        y_train = y_train_full[indices]
        
        if model_name == 'DecisionTreeClassifier':
            clf = DecisionTreeClassifier(**params, random_state=args.seed + i)
        elif model_name == 'SVC':
            # Paper doesn't specify random_state for SVC, but sklearn's SVC with poly kernel is deterministic given the data.
            # We pass a dummy state if supported, or rely on default behavior.
            clf = SVC(**params, random_state=args.seed + i)
        else:
            raise ValueError(f"Unknown model: {model_name}")
        
        clf.fit(X_train, y_train)
        y_pred = clf.predict(X_test)
        score = accuracy_score(y_test, y_pred)
        scores.append(score)
        
    train_seconds = time.time() - start_time
    mean_score = np.mean(scores)
    
    result = {
        "claim": args.claim,
        "seed": args.seed,
        "metric": "accuracy",
        "value": mean_score,
        "train_seconds": train_seconds,
        "n_train": len(X_train_full),
        "n_test": len(X_test),
        "config_overrides": args.set
    }
    
    print(json.dumps(result))


if __name__ == '__main__':
    main()
RECIPE_EOF
cat > smoke.sh <<'RECIPE_EOF'
#!/bin/bash
set -e
./venv/bin/python train.py --claim dt_fashion_1 --seed 42 --set data.dir=localdata > /tmp/smoke_out.json
echo "Smoke test completed. Output:"
tail -1 /tmp/smoke_out.json

RECIPE_EOF
mkdir -p venv localdata
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install numpy scikit-learn
wget -q https://github.com/zalandoresearch/fashion-mnist/raw/master/data/fashion/train-images-idx3-ubyte.gz -O localdata/train-images-idx3-ubyte.gz
wget -q https://github.com/zalandoresearch/fashion-mnist/raw/master/data/fashion/train-labels-idx1-ubyte.gz -O localdata/train-labels-idx1-ubyte.gz
wget -q https://github.com/zalandoresearch/fashion-mnist/raw/master/data/fashion/t10k-images-idx3-ubyte.gz -O localdata/t10k-images-idx3-ubyte.gz
wget -q https://github.com/zalandoresearch/fashion-mnist/raw/master/data/fashion/t10k-labels-idx1-ubyte.gz -O localdata/t10k-labels-idx1-ubyte.gz

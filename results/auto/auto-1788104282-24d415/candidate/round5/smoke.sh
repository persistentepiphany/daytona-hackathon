#!/bin/bash
set -e

# Test dataio loading
./venv/bin/python -c "
import dataio
import json
with open('config.json', 'r') as f:
    cfg = json.load(f)
    dataio.load_split.context = {'dataset': cfg['c_brf_monks']['dataset']}
    X, y = dataio.load_split(cfg['data']['dir'], 'train')
    print(f'Train shape: {X.shape}, Labels shape: {y.shape}')
    assert X.shape[0] == int(0.7 * 601), 'Train size mismatch for Monks-2'
    X, y = dataio.load_split(cfg['data']['dir'], 'test')
    print(f'Test shape: {X.shape}, Labels shape: {y.shape}')
    assert X.shape[0] == int(0.3 * 601), 'Test size mismatch for Monks-2'
"

# Test training run for one claim (monks)
# Using a small tree count for speed
RESULT=$(./venv/bin/python train.py --claim c_brf_monks --seed 42 --set n_trees_forest=5)
echo "Output: $RESULT"

# Validate JSON output
echo $RESULT | ./venv/bin/python -c "import sys, json; json.load(sys.stdin)"

echo "Smoke test passed."

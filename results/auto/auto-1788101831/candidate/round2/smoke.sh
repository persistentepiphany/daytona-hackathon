#!/bin/bash
set -e

# Fast sanity check of the pipeline
# Uses venv/bin/python and checks exit codes

mkdir -p localdata

echo "Testing data loading..."
venv/bin/python -c "
import json
import sys
import os
sys.path.insert(0, '.')

# Mock config for smoke test
with open('config.json', 'r') as f: cfg = json.load(f)

# Write minimal spec for monks
data_dir = cfg['data']['dir']
with open(os.path.join(data_dir, 'current_spec.json'), 'w') as f:
    json.dump({'dataset': 'monks', 'split_ratio': 0.7, 'seed': 42}, f)

import dataio
X, y = dataio.load_split(data_dir, 'train')
assert X.shape[0] > 0, 'Train set empty'
assert y.shape[0] > 0, 'Train labels empty'
print('Data OK')
"

echo "Testing model training (1 step)..."
venv/bin/python train.py --claim c1 --seed 42 --set c1.repetitions=1 --set c1.n_estimators=2 --set c1.k_candidates=2 > /tmp/smoke_output.json

# Validate JSON output
LAST_LINE=$(tail -n 1 /tmp/smoke_output.json)
echo "Output: $LAST_LINE"
venv/bin/python -c "import json, sys; d=json.loads('$LAST_LINE'); assert 'value' in d; assert 'metric' in d; assert d['metric'] == 'classification_error'"

echo "Smoke test passed."

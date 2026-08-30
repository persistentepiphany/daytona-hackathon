#!/bin/bash
set -e

# Quick sanity check of the pipeline
# 1. Check files exist
test -f venv/bin/python
test -f localdata/monks-2-train.csv
test -f localdata/breast-cancer-wisconsin.csv

# 2. Run a minimal training run (1 tree, 1 candidate, 1 split) on Monks
venv/bin/python train.py --claim c_brf_monks --seed 42 --set n_trees=1 --set n_candidates=1 --set n_splits=1 > /tmp/smoke_out.json

# 3. Verify JSON output
last_line=$(tail -n 1 /tmp/smoke_out.json)
python3 -c "import json; j=json.loads('$last_line'); assert 'value' in j; assert 0 <= j['value'] <= 1; print('Smoke test passed.')"

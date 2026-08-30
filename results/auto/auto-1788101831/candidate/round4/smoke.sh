#!/bin/bash
set -e

# Quick sanity check of the pipeline
# Use the 'monks' dataset (small) and minimal params for speed

# 1. Check data exists
if [ ! -f "localdata/monks-2.train" ]; then
    echo "Data file missing."
    exit 1
fi

# 2. Run a quick training loop with very few trees/candidates/splits
# Overriding config to be fast
./venv/bin/python train.py --claim c1 --seed 42 --set n_trees=2 --set n_candidates=2 --set max_splits=3 > /tmp/smoke_output.json

# 3. Check output is valid JSON and has required keys
LAST_LINE=$(tail -n 1 /tmp/smoke_output.json)
echo "Checking output: $LAST_LINE"

if python3 -c "import json, sys; data = json.load(sys.stdin); assert 'value' in data; assert 0 <= data['value'] <= 1" <<< "$LAST_LINE"; then
    echo "Smoke test passed."
    exit 0
else
    echo "Smoke test failed: invalid output or metric value."
    exit 1
fi

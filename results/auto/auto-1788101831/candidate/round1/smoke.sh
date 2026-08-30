#!/bin/bash
set -e

# Fast smoke test
# Run training with minimal parameters (small n_trees, k, p)
# Using the 'c1' claim (monks) as it loads quickly.

./venv/bin/python train.py --claim c1 --seed 42 --set n_trees=2 k_candidates=2 p_splits=2 n_repetitions=1

# Verify output is valid JSON
# (The pipeline runner checks the final line, we just check exit code)

echo "Smoke test passed."

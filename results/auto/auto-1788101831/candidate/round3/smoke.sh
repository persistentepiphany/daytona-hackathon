#!/bin/bash
set -e
venv/bin/python train.py --claim c1 --seed 42 --set c1.n_candidates=2 --set c1.n_trees=2 --set c1.n_splits=2
echo "Smoke test passed."

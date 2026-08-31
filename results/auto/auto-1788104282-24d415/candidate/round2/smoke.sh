#!/bin/bash
set -e

# Smoke test for Monks-2
# 1. Check data exists
if [ ! -f "localdata/monks-2.train" ]; then
  echo "Data missing" >&2
  exit 1
fi

# 2. Run training with minimal parameters to ensure code path executes
# Using k=1, m=1, p=2 for speed
venv/bin/python train.py --claim c_brf_monks --seed 42 --set k_candidates=1 --set m_trees=1 --set p_splits=2

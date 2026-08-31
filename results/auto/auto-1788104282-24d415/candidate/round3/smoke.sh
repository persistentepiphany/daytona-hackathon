#!/bin/bash
set -e

# Quick sanity check of the training pipeline
# Uses a tiny subset of data to ensure code runs end-to-end

# 1. Prepare dummy small data for a quick run
head -n 5 localdata/monks-2.train > localdata/smoke.train
head -n 2 localdata/monks-2.test > localdata/smoke.test

# We need to override the data loader to use these files or just rely on the existing ones 
# but with very few iterations. 
# Since train.py logic for loading is hardcoded to filenames, we can't easily switch files 
# without modifying code or config.
# However, monks-2 is very small anyway. 
# We will reduce the params to make it fast.

# Run training with minimal parameters (k=1, m=1, p=1)
# This should take seconds.

venv/bin/python train.py --claim c_brf_monks --seed 42 --set c_brf_monks.k_candidates=1 --set c_brf_monks.m_trees=1 --set c_brf_monks.p_splits=1

#!/bin/bash
set -e

# 1. Check dependencies exist
venv/bin/python -c "import numpy, sklearn; print('deps ok')"

# 2. Check dataio loads a small dataset (monks-2 is small)
# We set the global var to force monks-2 loading logic
venv/bin/python -c "
import sys
class DummyMain:
    CURRENT_DATASET = 'monks-2'
sys.modules['__main__'] = DummyMain()
import dataio
X, y = dataio.load_split('localdata', 'train')
assert X.shape[0] > 0
assert y.shape[0] > 0
print('dataio ok')
"

# 3. Run a tiny training loop (1 tree, 1 split, 1 candidate) to ensure code path works
# We use monks-2 as it's the smallest and already downloaded
venv/bin/python train.py --claim c_brf_monks --seed 42 --set data.dir=localdata --set c_brf_monks.m_trees=1 --set c_brf_monks.n_candidates=1 --set c_brf_monks.n_splits=1

echo 'smoke passed'

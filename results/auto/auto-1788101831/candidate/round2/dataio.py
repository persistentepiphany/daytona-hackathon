import numpy as np
import os
import pandas as pd

DATA_DIR = None

def load_split(data_dir, split):
    global DATA_DIR
    DATA_DIR = data_dir
    
    # Check if a dataset specification file exists (passed via claim/config)
    spec_path = os.path.join(data_dir, 'current_spec.json')
    if os.path.exists(spec_path):
        import json
        with open(spec_path, 'r') as f:
            spec = json.load(f)
        dataset_name = spec.get('dataset')
        split_ratio = spec.get('split_ratio', 0.7)
        seed = spec.get('seed', 0)
    else:
        # Fallback / Smoke test defaults
        dataset_name = 'monks'
        split_ratio = 0.7
        seed = 0
        
    X, y = _load_raw(dataset_name, data_dir)
    
    # Apply 70-30 split as described in paper
    # In production, seed is managed by train.py loop
    n_samples = X.shape[0]
    indices = np.arange(n_samples)
    np.random.seed(seed)
    np.random.shuffle(indices)
    
    split_idx = int(n_samples * split_ratio)
    
    if split == 'train':
        selected = indices[:split_idx]
    elif split == 'test':
        selected = indices[split_idx:]
    else:
        raise ValueError(f"Unknown split: {split}")
        
    return X[selected], y[selected]

def _load_raw(name, data_dir):
    if name == 'monks':
        # Monks-2 dataset
        train_path = os.path.join(data_dir, 'monks-2.train')
        test_path = os.path.join(data_dir, 'monks-2.test')
        
        # Monks files don't have headers, space separated. Last col is label.
        # Features are 1,2,3 (3 categories)
        d_train = np.loadtxt(train_path)
        d_test = np.loadtxt(test_path)
        
        data = np.vstack((d_train, d_test))
        X = data[:, :-1]
        y = data[:, -1]
        # Ensure binary classes {0, 1}
        y = (y == 1).astype(int)
        return X, y
        
    elif name == 'bcw':
        # Breast Cancer Wisconsin (Original)
        path = os.path.join(data_dir, 'breast-cancer-wisconsin.data')
        # Contains '?' for missing values. ID is col 0. Class is col 10.
        df = pd.read_csv(path, header=None)
        # Drop ID
        df = df.drop(columns=[0])
        # Handle missing: drop rows with '?'
        df = df.replace('?', np.nan).dropna()
        # Class is 2 (benign) or 4 (malignant). Map 2->0, 4->1
        y = (df[10].values == 4).astype(int)
        X = df.drop(columns=[10]).values.astype(float)
        return X, y

    else:
        raise ValueError(f"Dataset {name} not implemented")

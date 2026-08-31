import numpy as np
import os
import sys

def load_split(data_dir, split):
    """
    Loads the dataset specified in the global config context if running via train.py,
    or defaults to a safe dataset (monks-2) if imported standalone.
    Returns (X, y) for the requested split ('train' or 'test').
    """
    
    # Determine which dataset to load.
    # If train.py sets a global 'CURRENT_DATASET' variable, use it.
    # Otherwise, default to monks-2 for integrity checks.
    dataset_name = getattr(sys.modules['__main__'], 'CURRENT_DATASET', 'monks-2')

    if dataset_name == 'monks-2':
        # Monks-2 data format: 6 attributes + 1 label (0 or 1)
        # Paper: randomly split 70% train, 30% test. 
        # Since we have the original UCI train/test splits (size 169/432), we combine and resplit.
        # Files: monks-2.train, monks-2.test
        train_path = os.path.join(data_dir, 'monks-2.train')
        test_path = os.path.join(data_dir, 'monks-2.test')
        
        if not os.path.exists(train_path) or not os.path.exists(test_path):
            raise FileNotFoundError(f"Monks-2 data files not found in {data_dir}")
            
        # Load data: space separated
        d_train = np.loadtxt(train_path)
        d_test = np.loadtxt(test_path)
        
        X_all = np.vstack((d_train[:, :6], d_test[:, :6]))
        y_all = np.concatenate((d_train[:, 6], d_test[:, 6]))
        
        # One-hot encoding for the 6 categorical features (values 1, 2, 3)
        # Feature 0 has 3 vals, 1 has 3 vals, 2 has 2 vals, 3 has 3 vals, 4 has 4 vals, 5 has 2 vals.
        # Total dims = 3+3+2+3+4+2 = 17.
        X_processed = []
        ranges = [3, 3, 2, 3, 4, 2] 
        offset = 0
        for i, r in enumerate(ranges):
            col = X_all[:, i] - 1 # 0-indexed
            col_onehot = np.zeros((len(col), r))
            col_onehot[np.arange(len(col)), col.astype(int)] = 1
            X_processed.append(col_onehot)
        X_all = np.hstack(X_processed)
        
        # Random split 70/30
        np.random.seed(0) # Fixed seed for reproducibility of the split definition
        indices = np.arange(len(X_all))
        np.random.shuffle(indices)
        split_idx = int(0.7 * len(X_all))
        train_idx, test_idx = indices[:split_idx], indices[split_idx:]
        
        if split == 'train':
            return X_all[train_idx], y_all[train_idx]
        elif split == 'test':
            return X_all[test_idx], y_all[test_idx]

    elif dataset_name == 'breast_cancer_wisconsin':
        # Breast Cancer Wisconsin (Original)
        # 699 samples, 9 attributes + ID + Class.
        # 16 samples have missing '?', usually handled by removal or imputation.
        # Paper doesn't specify, but 3-fold CV + training usually requires clean data.
        # We will remove rows with '?' for robustness.
        path = os.path.join(data_dir, 'bcw.data')
        if not os.path.exists(path):
             raise FileNotFoundError(f"BCW data file not found in {data_dir}")
        
        # Load text, handle '?' as NaN
        data = []
        with open(path, 'r') as f:
            for line in f:
                if '?' in line:
                    continue # Skip missing values
                parts = line.strip().split(',')
                # Skip ID (index 0), take features 1-9, label 10
                row = [int(x) for x in parts[1:]]
                data.append(row)
        
        data = np.array(data)
        X = data[:, :-1]
        y = data[:, -1]
        # Labels: 2 for benign, 4 for malignant. Map to 0 and 1.
        y = (y == 4).astype(int)
        
        # 70/30 Split
        np.random.seed(0)
        indices = np.arange(len(X))
        np.random.shuffle(indices)
        split_idx = int(0.7 * len(X))
        train_idx, test_idx = indices[:split_idx], indices[split_idx:]
        
        if split == 'train':
            return X[train_idx], y[train_idx]
        elif split == 'test':
            return X[test_idx], y[test_idx]
            
    else:
        raise ValueError(f"Unknown dataset: {dataset_name}")

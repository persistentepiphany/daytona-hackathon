import numpy as np
import os

def load_split(data_dir, split):
    """Loads a dataset based on the data_dir and split type.
    
    Args:
        data_dir: Path to the local data folder.
        split: 'train' or 'test'.
        
    Returns:
        X, y as numpy arrays.
    """
    # Determine dataset name from dir path structure or hardcoded mapping
    # The paper requests 'monks-2' and 'breast-cancer-wisconsin'
    # We check existence of specific files to infer dataset.
    
    monks_train_path = os.path.join(data_dir, 'monks-2-train.csv')
    bcw_path = os.path.join(data_dir, 'breast-cancer-wisconsin.csv')
    
    if os.path.exists(monks_train_path):
        # Monks-2 Dataset
        # Features are indices 1-6 (6 features), Label is index 0 (0 or 1)
        if split == 'train':
            raw = np.loadtxt(monks_train_path, delimiter=',')
        elif split == 'test':
            raw = np.loadtxt(os.path.join(data_dir, 'monks-2-test.csv'), delimiter=',')
        else:
            raise ValueError("Split must be 'train' or 'test'")
        
        # Monks data in UCI is: Label, A1, A2, A3, A4, A5, A6
        X = raw[:, 1:7]
        y = raw[:, 0]
        
    elif os.path.exists(bcw_path):
        # Breast Cancer Wisconsin Dataset
        # Features are indices 1-9. Label is index 10 (2 for benign, 4 for malignant).
        # Handle missing values (marked as '?') by dropping rows.
        raw = np.genfromtxt(bcw_path, delimiter=',')
        # Filter rows with NaN (missing values)
        valid_mask = ~np.isnan(raw).any(axis=1)
        raw = raw[valid_mask]
        
        # Paper mentions 70% train / 30% test split randomly.
        # We use a fixed seed for reproducibility in the load_split function
        # to ensure the split is consistent for the runner.
        # However, since 'train.py' handles the splitting for BRF (section 6.2),
        # we might return the full dataset here or split it.
        # Given the split argument, we must split.
        
        rng = np.random.RandomState(42)
        indices = np.arange(len(raw))
        rng.shuffle(indices)
        
        split_idx = int(0.7 * len(raw))
        train_indices = indices[:split_idx]
        test_indices = indices[split_idx:]
        
        if split == 'train':
            split_raw = raw[train_indices]
        elif split == 'test':
            split_raw = raw[test_indices]
        else:
            raise ValueError("Split must be 'train' or 'test'")
            
        X = split_raw[:, 1:10] # 9 features
        y = split_raw[:, 10]
        # Map labels 2->0, 4->1 for binary classification
        y = (y == 4).astype(int)
    else:
        raise FileNotFoundError(f"Dataset not found in {data_dir}")
        
    return X, y

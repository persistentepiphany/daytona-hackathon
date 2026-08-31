import numpy as np
import os

def load_monks(data_dir):
    train_path = os.path.join(data_dir, 'monks-2.train')
    test_path = os.path.join(data_dir, 'monks-2.test')
    # monks format: label (0/1) f1 f2 f3 f4 f5 f6. Space separated.
    train_raw = np.loadtxt(train_path)
    test_raw = np.loadtxt(test_path)
    
    X_train = train_raw[:, 1:]
    y_train = train_raw[:, 0]
    X_test = test_raw[:, 1:]
    y_test = test_raw[:, 0]
    return X_train, y_train, X_test, y_test

def load_bcw(data_dir):
    path = os.path.join(data_dir, 'bcw.data')
    # bcw format: ID, Clump, ... , Class. Comma separated.
    # Class: 2 for benign, 4 for malignant. We map to 0/1.
    raw = np.loadtxt(path, delimiter=',')
    
    # Handle missing values marked as '?'
    # The dataset has '?' in column 6 (index 5 in 0-based) for some rows.
    # We drop rows with missing values.
    mask = np.all(raw != '?', axis=1)
    raw = raw[mask].astype(float)
    
    X = raw[:, 1:-1] # Drop ID (col 0) and Class (last col)
    y = raw[:, -1]
    
    # Map 2 -> 0 (benign), 4 -> 1 (malignant)
    y = (y == 4).astype(int)
    
    return X, y

def load_split(data_dir, split):
    """
    Returns (X, y) for the requested split.
    split: 'train' or 'test'
    Note: For this implementation, we rely on the dataset's intrinsic train/test split if available
    (like Monks), or we perform a 70/30 split (like BCW) here.
    Because the pipeline calls load_split separately for train and test, we must ensure
    consistency. We will check for a cached split or perform it deterministically.
    """
    dataset_id = "unknown"
    # Determine which dataset is requested based on available files
    if os.path.exists(os.path.join(data_dir, 'monks-2.train')):
        dataset_id = "monks-2"
    elif os.path.exists(os.path.join(data_dir, 'bcw.data')):
        dataset_id = "bcw"
    else:
        raise FileNotFoundError("No known dataset found in data_dir")

    if dataset_id == "monks-2":
        X_tr, y_tr, X_te, y_te = load_monks(data_dir)
        if split == "train":
            return X_tr, y_tr
        else:
            return X_te, y_te
    
    elif dataset_id == "breast_cancer_wisconsin":
        # We perform a fixed 70/30 split for reproducibility as per paper "randomly split..."
        # We use a fixed seed for the split itself to ensure train/test are consistent across calls.
        X, y = load_bcw(data_dir)
        from sklearn.model_selection import train_test_split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.3, random_state=42, stratify=y
        )
        if split == "train":
            return X_train, y_train
        else:
            return X_test, y_test
    
    raise ValueError(f"Unknown split or dataset: {dataset_id}, {split}")

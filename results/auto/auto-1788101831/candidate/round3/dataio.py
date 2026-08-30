import numpy as np
import pandas as pd
import os

def load_split(data_dir, split):
    ds_name = os.path.basename(data_dir) # Not used, reading specific files
    
    # Load Monks-2
    # Format: 6 attributes + class (0/1) space separated
    monks_path = os.path.join(data_dir, 'monks-2.train')
    if os.path.exists(monks_path):
        data = np.loadtxt(monks_path)
        X, y = data[:, :-1], data[:, -1]
        # Ensure y is 0/1
        y = (y > 0).astype(int)
    else:
        # Load BCW
        # Format: ID, 9 features, Class(2/4). Separated by commas. Missing values '?'
        bcw_path = os.path.join(data_dir, 'breast-cancer-wisconsin.data')
        if os.path.exists(bcw_path):
            df = pd.read_csv(bcw_path, header=None)
            # Handle missing values (paper doesn't specify, we drop rows with '?' for robustness)
            df.replace('?', np.nan, inplace=True)
            df.dropna(inplace=True)
            # Column 0 is ID, Column 10 is Class. Features 1-9.
            X = df.iloc[:, 1:10].values.astype(float)
            y = df.iloc[:, 10].values.astype(int)
            # Class is 2 (benign) and 4 (malignant). Map to 0 and 1.
            y = (y == 4).astype(int)
        else:
            raise FileNotFoundError("No recognized dataset found in localdata")

    # Split 70-30
    n_samples = X.shape[0]
    n_train = int(0.7 * n_samples)
    indices = np.arange(n_samples)
    
    # Split index
    mid = n_train
    
    if split == "train":
        return X[:mid], y[:mid]
    elif split == "test":
        return X[mid:], y[mid:]
    else:
        raise ValueError("Unknown split")

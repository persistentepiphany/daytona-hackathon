import numpy as np
import os
import pandas as pd

def load_split(data_dir, split):
    # Note: For this paper reproduction, the split is randomized in train.py
    # based on the repetition seed. This function loads the full dataset.
    
    # Identify dataset by looking at available files (simple heuristic)
    monks_path = os.path.join(data_dir, 'monks-2.train')
    bcw_path = os.path.join(data_dir, 'breast-cancer-wisconsin.data')
    ilpd_path = os.path.join(data_dir, 'ilpd.csv')

    if os.path.exists(monks_path):
        # Monks-2 dataset
        # Format: 6 features + 1 target (0/1). Space separated.
        # We map 0->-1, 1->1 for consistency with paper (-1, 1) labels if desired, 
        # but standard sklearn expects 0/1. We will use 0/1.
        data = np.loadtxt(monks_path)
        X = data[:, :-1]
        y = data[:, -1].astype(int)
        return X, y
    elif os.path.exists(bcw_path):
        # Breast Cancer Wisconsin
        # CSV format, 10 columns (ID + 9 features + target).
        # Missing values are '?'. We drop them (as is standard for this dataset).
        data = pd.read_csv(bcw_path, header=None)
        data = data[data[6] != '?']  # Column 6 (index 6) often has '?', simplified handling
        # Actually, drop rows with any '?' in cols 1-9 (indices 1-9 in 0-based 1..10)
        # The data columns are 1-9 (indices 1 to 9). Col 10 is label.
        # Let's filter generally.
        data.replace('?', np.nan, inplace=True)
        data.dropna(inplace=True)
        
        X = data.iloc[:, 1:-1].values.astype(float) # Features are cols 2-10 (indices 1 to 9)
        y = data.iloc[:, -1].values.astype(int)     # Label is col 11 (index 10)
        # Labels are 2 (benign) and 4 (malignant). Map to 0 and 1.
        y = (y / 2) - 1
        return X, y
    elif os.path.exists(ilpd_path):
        # ILPD dataset
        # CSV. Last column is 'Selector' (1=Liver Patient, 2=Non-Liver Patient).
        data = pd.read_csv(ilpd_path)
        # Drop gender column (non-numeric) to simplify, or one-hot encode. 
        # For RF, let's one-hot encode or drop. Paper uses it, so we should keep.
        # Map Gender Female->0, Male->1.
        data['Gender'] = data['Gender'].map({'Female': 0, 'Male': 1})
        
        # Handle missing values if any (drop for simplicity)
        data.dropna(inplace=True)
        
        X = data.iloc[:, :-1].values.astype(float)
        y = data.iloc[:, -1].values.astype(int)
        # Map 1 -> 1, 2 -> 0 (or similar binary mapping)
        y = np.where(y == 1, 1, 0)
        return X, y
    else:
        raise FileNotFoundError(f"No recognized dataset found in {data_dir}")

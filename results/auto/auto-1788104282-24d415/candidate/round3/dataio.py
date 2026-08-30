import numpy as np
import os
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

# Map claim IDs to datasets
DATASET_MAP = {
    "c_brf_monks": "monks-2",
    "c_brf_bcw": "breast_cancer_wisconsin"
}

def _load_monks(data_dir):
    # Monks-2 from UCI
    train_path = os.path.join(data_dir, 'monks-2.train')
    test_path = os.path.join(data_dir, 'monks-2.test')
    
    # Format: class attrib1 attrib2 attrib3 attrib4 attrib5 attrib6
    train_data = np.loadtxt(train_path)
    test_data = np.loadtxt(test_path)
    
    X_train = train_data[:, 1:]
    y_train = train_data[:, 0]
    X_test = test_data[:, 1:]
    y_test = test_data[:, 0]
    
    # Paper uses 70/30 split. UCI monks provides fixed splits. 
    # We combine and resplit to strictly satisfy 70/30 requirement if needed,
    # but usually fixed splits are accepted. We stick to strict 70/30.
    X = np.vstack((X_train, X_test))
    y = np.hstack((y_train, y_test))
    
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y
    )
    
    return (X_train, y_train), (X_test, y_test)

def _load_bcw(data_dir):
    # Breast Cancer Wisconsin (Original)
    path = os.path.join(data_dir, 'breast-cancer-wisconsin.data')
    # Column names: ID, Clump, UnifCellSize, UnifCellShape, MargAdh, SEpithSize, 
    # BareNuc, BlandChrom, NormNucl, Mitoses, Class
    cols = ['ID', 'Clump', 'UnifSize', 'UnifShape', 'MargAdh', 'SEpith', 
            'BareNuc', 'BlandChrom', 'NormNucl', 'Mit', 'Class']
    df = pd.read_csv(path, names=cols)
    
    # Remove missing values (marked as '?')
    df = df.replace('?', np.nan).dropna()
    
    # Remove ID
    X = df.drop(['ID', 'Class'], axis=1).values.astype(float)
    y = df['Class'].values
    # Map 2/4 to 0/1
    y = (y == 4).astype(int)
    
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y
    )
    
    return (X_train, y_train), (X_test, y_test)

def load_split(data_dir, split):
    # Since we don't have the dataset name passed directly, 
    # we try to load all supported datasets and return the first valid one.
    # In the train.py flow, we know the claim ID, so we load the specific one.
    # This function signature is required by the contract.
    # We will add a mechanism in train.py to call the specific loader.
    # For standalone integrity check, we default to Monks if available.
    
    monks_train = os.path.join(data_dir, 'monks-2.train')
    bcw_train = os.path.join(data_dir, 'breast-cancer-wisconsin.data')
    
    if os.path.exists(monks_train):
        return _load_monks(data_dir)[0] if split == 'train' else _load_monks(data_dir)[1]
    elif os.path.exists(bcw_train):
        return _load_bcw(data_dir)[0] if split == 'train' else _load_bcw(data_dir)[1]
    
    raise FileNotFoundError("No supported dataset found in localdata")

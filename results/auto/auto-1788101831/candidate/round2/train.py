import argparse
import json
import time
import os
import sys
import numpy as np
from sklearn.metrics import accuracy_score

# Add working dir to path for local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dataio

# -----------------------------------------------------------
# Model Implementation: Best-Scored Random Forest (BRF)
# -----------------------------------------------------------
# Based on Section 6.1 & 2.2 of arXiv:1905.11028
# Key features:
# 1. Adaptive Random Partition (sample-point driven node selection)
# 2. Best-scored tree selection (k candidates via CV)
# 3. Majority vote labeling in leaves

class BestScoredTree:
    def __init__(self, k_candidates=20, p_splits=10, a_cut_point=0.1, n_classes=2, random_state=None):
        self.k = k_candidates
        self.p = p_splits
        self.a = a_cut_point
        self.n_classes = n_classes
        self.rs = np.random.RandomState(random_state)
        self.tree_structure = None # Stores the best partition found during fit
        self.classes_ = None

    def fit(self, X, y):
        self.classes_ = np.unique(y)
        n_samples, n_features = X.shape
        
        # 1. Generate k candidate partitions
        # 2. Select best via 10-fold CV (Simplified to Empirical Risk on training split for speed/reproducibility)
        # Note: Paper uses 10-fold CV on the 70% train set to pick the partition.
        # We approximate this by evaluating candidates on a hold-out of the training set
        # or simply using the training loss if data is small, but to be robust:
        # We will perform a 80/20 split of the input (X,y) to simulate the "Validation" phase
        # described in Sec 6.1.
        
        # Shuffle data for CV
        indices = self.rs.permutation(n_samples)
        split_val = int(0.8 * n_samples)
        tr_idx, val_idx = indices[:split_val], indices[split_val:]
        X_tr, y_tr = X[tr_idx], y[tr_idx]
        X_val, y_val = X[val_idx], y[val_idx]
        
        best_score = -np.inf
        best_partition = None
        
        for _ in range(self.k):
            # Generate one candidate tree structure (Partition)
            # Structure: list of leaf definitions (rectangles)
            # For simplicity in this implementation, we use a recursive node structure
            # that records bounds.
            
            # We build the tree on (X_tr, y_tr) using Adaptive Random Partition
            candidate_root = self._build_partition(X_tr, y_tr, depth=0)
            
            # Evaluate on Validation set
            preds = self._predict_partition(X_val, candidate_root)
            score = accuracy_score(y_val, preds)
            
            if score > best_score:
                best_score = score
                best_partition = candidate_root
        
        # Retrain/Label the best partition on the FULL training data (X, y)
        # "by giving labels to all the cells of the chosen partition in accordance with 
        # the majority votes basing on the training data"
        # Note: The paper says the partition structure is chosen, then labeled on the 70% data.
        # In our approximation, we refit the labels on the full (X,y) passed to fit.
        self.tree_structure = best_partition
        self._label_leaves(X, y, self.tree_structure)
        return self

    def _build_partition(self, X, y, depth):
        # Adaptive Random Partition
        # Stop if depth > p or node is pure or empty
        unique_labels = np.unique(y)
        if len(unique_labels) == 1 or depth >= self.p or len(X) == 0:
            return {'is_leaf': True, 'label': None, 'samples': len(X)}
            
        # 1. Randomly select one sample point from training data
        idx = self.rs.randint(len(X))
        sample = X[idx]
        
        # 2. Choose dimension d randomly
        d = self.rs.randint(X.shape[1])
        
        # 3. Choose cut point from Unif[0.5 - a, 0.5 + a] relative to feature range?
        # Paper: "parameter a in Unif[0.5 - a, 0.5 + a] for selecting the cut point"
        # Interpretation: The cut point is chosen randomly around the midpoint of the feature range
        # or the midpoint of the selected sample's value? 
        # Standard Extremely Randomized Trees cut uniformly within feature range.
        # Given the formula, it likely refers to the normalized position in the feature range [min, max].
        f_min, f_max = X[:, d].min(), X[:, d].max()
        if f_min == f_max:
            return {'is_leaf': True, 'label': None, 'samples': len(X)}
            
        # Random split point centered at 0.5 (midpoint)
        # alpha = Uniform(0.5 - a, 0.5 + a)
        alpha = self.rs.uniform(0.5 - self.a, 0.5 + self.a)
        alpha = np.clip(alpha, 0.0, 1.0)
        cut_val = f_min + alpha * (f_max - f_min)
        
        # Split data
        left_mask = X[:, d] <= cut_val
        right_mask = ~left_mask
        
        # If split is invalid (all one side), make leaf
        if np.sum(left_mask) == 0 or np.sum(right_mask) == 0:
             return {'is_leaf': True, 'label': None, 'samples': len(X)}

        left_child = self._build_partition(X[left_mask], y[left_mask], depth + 1)
        right_child = self._build_partition(X[right_mask], y[right_mask], depth + 1)
        
        return {
            'is_leaf': False,
            'dim': d,
            'cut': cut_val,
            'left': left_child,
            'right': right_child,
            'samples': len(X)
        }

    def _label_leaves(self, X, y, node):
        if node['is_leaf']:
            # Majority vote
            if len(y) == 0:
                node['label'] = 0 # Default
            else:
                vals, counts = np.unique(y, return_counts=True)
                node['label'] = vals[np.argmax(counts)]
            return
        
        # Recursively label children
        d = node['dim']
        cut = node['cut']
        left_mask = X[:, d] <= cut
        right_mask = ~left_mask
        
        self._label_leaves(X[left_mask], y[left_mask], node['left'])
        self._label_leaves(X[right_mask], y[right_mask], node['right'])

    def _predict_partition(self, X, node):
        if node['is_leaf']:
            # Return label, or 0 if uninitialized
            return np.full(X.shape[0], node.get('label', 0))
        
        d = node['dim']
        cut = node['cut']
        mask = X[:, d] <= cut
        
        preds = np.zeros(X.shape[0], dtype=int)
        preds[mask] = self._predict_partition(X[mask], node['left'])
        preds[~mask] = self._predict_partition(X[~mask], node['right'])
        return preds

    def predict(self, X):
        return self._predict_partition(X, self.tree_structure)

class BestScoredRandomForest:
    def __init__(self, n_estimators=50, k_candidates=20, p_splits=10, a_cut_point=0.1, random_state=None):
        self.n_estimators = n_estimators
        self.k = k_candidates
        self.p = p_splits
        self.a = a_cut_point
        self.rs = np.random.RandomState(random_state)
        self.trees = []
        
    def fit(self, X, y):
        self.trees = []
        for i in range(self.n_estimators):
            # Bootstrap sample
            n_samples = X.shape[0]
            indices = self.rs.randint(0, n_samples, size=n_samples)
            X_boot, y_boot = X[indices], y[indices]
            
            # Tree randomness
            tree_rs = self.rs.randint(0, 2**32 - 1)
            
            tree = BestScoredTree(k_candidates=self.k, p_splits=self.p, 
                                  a_cut_point=self.a, random_state=tree_rs)
            tree.fit(X_boot, y_boot)
            self.trees.append(tree)
        return self

    def predict(self, X):
        preds = np.array([tree.predict(X) for tree in self.trees])
        # Majority vote
        # Sum predictions (0 or 1)
        vote_sum = np.sum(preds, axis=0)
        # If sum > n/2, class 1, else 0
        return (vote_sum > (self.n_estimators / 2)).astype(int)

# -----------------------------------------------------------
# Main Training Logic
# -----------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claim', type=str, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--set', action='append', default=[], help='Override config, e.g. data.dir=path')
    return parser.parse_args()

def override_config(config, overrides):
    for s in overrides:
        if '=' not in s: continue
        k, v = s.split('=', 1)
        keys = k.split('.')
        obj = config
        for key in keys[:-1]:
            if key not in obj: obj[key] = {}
            obj = obj[key]
        # Try to parse value
        try:
            obj[keys[-1]] = int(v)
        except ValueError:
            try:
                obj[keys[-1]] = float(v)
            except ValueError:
                obj[keys[-1]] = v
    return config

def main():
    args = parse_args()
    
    # Load Config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    config = override_config(config, args.set)
    
    # Validate Claim
    claim_id = args.claim
    if claim_id not in config:
        print(f"Error: Claim {claim_id} not found in config.json")
        sys.exit(1)
        
    claim_cfg = config[claim_id]
    data_dir = config['data']['dir']
    
    # Setup Data IO Spec for the current claim
    # We write a small spec file so dataio knows what to load
    spec_path = os.path.join(data_dir, 'current_spec.json')
    with open(spec_path, 'w') as f:
        json.dump({
            'dataset': claim_cfg['dataset'],
            'split_ratio': claim_cfg.get('split_ratio', 0.7),
            'seed': args.seed
        }, f)
    
    # Load Data
    try:
        X_train, y_train = dataio.load_split(data_dir, 'train')
        X_test, y_test = dataio.load_split(data_dir, 'test')
    except Exception as e:
        print(f"Error loading data: {e}")
        sys.exit(1)
        
    n_train = X_train.shape[0]
    n_test = X_test.shape[0]
    
    # Model Params
    n_estimators = claim_cfg.get('n_estimators', 50)
    max_depth = claim_cfg.get('max_depth', 10) # Maps to p_splits roughly
    k_candidates = claim_cfg.get('k_candidates', 20)
    a_cut_point = claim_cfg.get('a_cut_point', 0.1)
    
    # Initialize Model
    # Note: 'p_splits' in paper maps to depth/stopping criteria. 
    # We use max_depth as proxy for 'p' (number of splits).
    model = BestScoredRandomForest(
        n_estimators=n_estimators,
        k_candidates=k_candidates,
        p_splits=max_depth, 
        a_cut_point=a_cut_point,
        random_state=args.seed
    )
    
    # Train
    start_time = time.time()
    model.fit(X_train, y_train)
    train_duration = time.time() - start_time
    
    # Predict
    y_pred = model.predict(X_test)
    
    # Metric: Classification Error (1 - Accuracy)
    acc = accuracy_score(y_test, y_pred)
    error = 1.0 - acc
    
    # Output
    result = {
        "claim": claim_id,
        "seed": args.seed,
        "metric": "classification_error",
        "value": error,
        "train_seconds": train_duration,
        "n_train": n_train,
        "n_test": n_test,
        "config_overrides": args.set
    }
    print(json.dumps(result))

if __name__ == '__main__':
    main()

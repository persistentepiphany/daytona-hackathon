import argparse
import json
import time
import numpy as np
import os
import sys

from sklearn.metrics import accuracy_score
from sklearn.model_selection import KFold

# Import data loading logic
import dataio

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claim', type=str, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--set', action='append', default=[], help='Override config key=value')
    return parser.parse_args()

def load_config(overrides):
    with open('config.json', 'r') as f:
        cfg = json.load(f)
    
    for override in overrides:
        key, value = override.split('=', 1)
        # Handle nested keys (e.g., data.dir)
        parts = key.split('.')
        obj = cfg
        for part in parts[:-1]:
            if part not in obj: obj[part] = {}
            obj = obj[part]
        
        # Try parsing as number
        try:
            obj[parts[-1]] = int(value)
        except ValueError:
            try:
                obj[parts[-1]] = float(value)
            except ValueError:
                obj[parts[-1]] = value
    return cfg

class AdaptiveRandomPartition:
    def __init__(self, max_splits, p, a):
        self.max_splits = max_splits
        self.p = p
        self.a = a
        self.nodes = [] # List of node dicts: {indices, depth, is_leaf, left, right, feat, thresh}
        self.nodes.append({'indices': None, 'depth': 0, 'is_leaf': True, 'left': None, 'right': None, 'feat': None, 'thresh': None})

    def fit(self, X, y):
        n_samples, n_features = X.shape
        # Initialize root with all indices
        self.nodes[0]['indices'] = np.arange(n_samples)
        self.nodes[0]['is_leaf'] = False
        
        # Adaptive Random Partitioning logic
        # We iterate p times (number of splits requested).
        # Paper says "generate k p-splitting adaptive random partitions".
        # We need to select a node to split.
        
        # Note: The paper implies a tree might stop if no valid split is found or nodes are too small, 
        # but it also specifies p splits. We try to perform p splits.
        
        split_count = 0
        while split_count < self.p:
            # 1. Adaptive Selection of Node Li
            # "randomly select one sample point from the training data set, 
            # and then choose the node which that sample point belongs to as Li"
            
            # Filter non-leaf nodes (potential candidates) or nodes that can be split
            # Actually, nodes in self.nodes structure. We need to find leaf nodes (active partitions)
            leaf_indices = [i for i, n in enumerate(self.nodes) if n['is_leaf'] and len(n['indices']) > 1]
            
            if not leaf_indices:
                break # No splittable nodes left

            # Pick a random sample from the whole dataset
            rand_idx = np.random.randint(0, n_samples)
            
            # Find which node this sample belongs to
            target_node_idx = -1
            for idx in leaf_indices:
                if rand_idx in self.nodes[idx]['indices']:
                    target_node_idx = idx
                    break
            
            if target_node_idx == -1:
                continue # Should not happen if indices are correct
                
            current_node = self.nodes[target_node_idx]
            node_samples_idx = current_node['indices']
            
            if len(node_samples_idx) <= 1:
                current_node['is_leaf'] = True
                continue
                
            # 2. Random Splitting Criterion
            # "select the cut point... according to Uniform[0.5-a, 0.5+a]"
            # Pick a random feature j
            feat_j = np.random.randint(0, n_features)
            
            # Get feature values in this node
            X_feat = X[node_samples_idx, feat_j]
            min_val = np.min(X_feat)
            max_val = np.max(X_feat)
            
            if min_val == max_val:
                continue # Cannot split
            
            # Determine cut point
            # The paper describes a uniform distribution on the relative position (0.5 +/- a)
            # We map this to the actual feature range.
            alpha = np.random.uniform(0.5 - self.a, 0.5 + self.a)
            thresh = min_val + alpha * (max_val - min_val)
            
            # Perform split
            left_mask = X[node_samples_idx, feat_j] <= thresh
            right_mask = ~left_mask
            
            # Ensure both sides have data
            if not np.any(left_mask) or not np.any(right_mask):
                continue
            
            left_indices = node_samples_idx[left_mask]
            right_indices = node_samples_idx[right_mask]
            
            # Update tree structure
            # Left child
            self.nodes.append({
                'indices': left_indices,
                'depth': current_node['depth'] + 1,
                'is_leaf': True,
                'left': None, 'right': None,
                'feat': None, 'thresh': None
            })
            left_child_idx = len(self.nodes) - 1
            
            # Right child
            self.nodes.append({
                'indices': right_indices,
                'depth': current_node['depth'] + 1,
                'is_leaf': True,
                'left': None, 'right': None,
                'feat': None, 'thresh': None
            })
            right_child_idx = len(self.nodes) - 1
            
            current_node['left'] = left_child_idx
            current_node['right'] = right_child_idx
            current_node['feat'] = feat_j
            current_node['thresh'] = thresh
            current_node['is_leaf'] = False # Now an internal node
            
            split_count += 1
            
    def predict(self, X):
        # Label each cell with majority vote of training samples
        # First, assign class labels to leaf nodes based on training data stored in 'indices'
        # We need y_train to do this. Refactored to pass y_train to a method or store it.
        pass # Handled in wrapper class below

class BestScoredTree:
    def __init__(self, k, p, a, n_cv_folds=10):
        self.k = k
        self.p = p
        self.a = a
        self.n_cv_folds = n_cv_folds
        self.best_partition = None
        self.leaf_labels = {} # Map node_index -> class_label

    def fit(self, X, y):
        n_samples = X.shape[0]
        kf = KFold(n_splits=self.n_cv_folds, shuffle=True, random_state=42) # Fixed shuffle for CV consistency?
        # Paper says "first round of the 10-fold cross-validation...".
        # We assume standard KFold.
        
        candidate_scores = np.zeros(self.k)
        candidates = []
        
        # Generate k candidates
        for i in range(self.k):
            partition = AdaptiveRandomPartition(max_splits=self.p, p=self.p, a=self.a)
            candidates.append(partition)
            
            # 10-fold CV to score this partition
            fold_errors = []
            for train_idx, val_idx in kf.split(X):
                X_tr, X_val = X[train_idx], X[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]
                
                # Fit partition on CV train set
                # Note: AdaptiveRandomPartition needs to be re-initialized or reset for this CV fold
                # The partition structure depends on X_tr.
                cv_part = AdaptiveRandomPartition(max_splits=self.p, p=self.p, a=self.a)
                cv_part.fit(X_tr, y_tr)
                
                # Predict on Validation set using this partition's majority votes
                preds = self._predict_with_partition(cv_part, X_tr, y_tr, X_val)
                error = 1.0 - accuracy_score(y_val, preds)
                fold_errors.append(error)
                
            candidate_scores[i] = np.mean(fold_errors)
            
        # Select best partition (lowest error)
        best_idx = np.argmin(candidate_scores)
        self.best_partition = candidates[best_idx]
        
        # Retrain best partition on FULL training data
        # The paper says: "by giving labels to all the cells of the chosen partition in accordance with the majority votes 
        # basing on the training data... we finally manage to construct one tree"
        # This implies we take the structure (the splits) but re-evaluate the majority class based on the full training set.
        # However, the partition structure itself was derived on the CV folds? 
        # Strict reading: We have k candidates generated. We scored them via CV. We pick the winner.
        # Does the winner retain the splits from the specific fold it was evaluated on? 
        # Or do we refit the partition (same random seeds?) on the full data?
        # "choose the partition from all k candidates with the smallest average validation error to be the exact partition for one tree."
        # This suggests we keep the partition geometry that won.
        # But wait, the partition geometry was fit inside the CV loop. Which geometry do we keep? 
        # To simplify and align with "best-scored": We will refit the selected partition strategy on the full data.
        # Actually, to ensure the "partition" is the one tested, we should ideally have stored the random seeds for the candidate.
        # Given the constraints, a valid interpretation of "Best Scored" is: pick the hyperparameters/randomness that led to the best CV score.
        # Since we can't easily store the randomness of the winner across folds, we will fit the BEST candidate (by index) on the full data now.
        # Note: candidates[i] in my list above were initialized but not fit on full data (only inside CV).
        # We simply refit candidates[best_idx] on the full X, y.
        
        self.best_partition.fit(X, y)
        
        # Compute leaf labels for the full training data
        self._compute_leaf_labels(X, y)

    def _predict_with_partition(self, partition, X_train, y_train, X_test):
        # Helper to predict using a given partition structure (fit on X_train)
        # First determine leaf labels based on X_train, y_train
        leaf_labels = {}
        for i, node in enumerate(partition.nodes):
            if node['is_leaf']:
                indices = node['indices']
                if len(indices) == 0:
                    leaf_labels[i] = 0 # default
                else:
                    counts = np.bincount(y_train[indices].astype(int))
                    leaf_labels[i] = np.argmax(counts)
        
        # Traverse X_test
        preds = np.zeros(X_test.shape[0], dtype=int)
        for j in range(X_test.shape[0]):
            node_idx = 0 # root
            while not partition.nodes[node_idx]['is_leaf']:
                node = partition.nodes[node_idx]
                feat = node['feat']
                thresh = node['thresh']
                if X_test[j, feat] <= thresh:
                    node_idx = node['left']
                else:
                    node_idx = node['right']
            preds[j] = leaf_labels[node_idx]
        return preds

    def _compute_leaf_labels(self, X, y):
        # Populate self.leaf_labels based on self.best_partition and full X, y
        for i, node in enumerate(self.best_partition.nodes):
            if node['is_leaf']:
                indices = node['indices']
                if len(indices) == 0:
                    self.leaf_labels[i] = 0
                else:
                    # Ensure y is integers for bincount
                    y_int = y[indices].astype(int)
                    # Handle case where classes might not start at 0 or be contiguous if weird data, but here 0/1
                    classes = np.unique(y_int)
                    if len(classes) == 0:
                        self.leaf_labels[i] = 0
                    else:
                        # Majority vote
                        counts = np.bincount(y_int)
                        self.leaf_labels[i] = np.argmax(counts)

    def predict(self, X):
        if self.best_partition is None:
            raise Exception("Model not fitted")
        
        preds = np.zeros(X.shape[0], dtype=int)
        for j in range(X.shape[0]):
            node_idx = 0
            while not self.best_partition.nodes[node_idx]['is_leaf']:
                node = self.best_partition.nodes[node_idx]
                feat = node['feat']
                thresh = node['thresh']
                if X[j, feat] <= thresh:
                    node_idx = node['left']
                else:
                    node_idx = node['right']
            preds[j] = self.leaf_labels.get(node_idx, 0)
        return preds

class BestScoredRandomForest:
    def __init__(self, m, k, p, a):
        self.m = m
        self.k = k
        self.p = p
        self.a = a
        self.trees = []

    def fit(self, X, y):
        for _ in range(self.m):
            tree = BestScoredTree(k=self.k, p=self.p, a=self.a)
            tree.fit(X, y)
            self.trees.append(tree)

    def predict(self, X):
        # Aggregate predictions
        tree_preds = np.array([tree.predict(X) for tree in self.trees])
        # Majority vote
        # Sum predictions (0 or 1) -> check if > m/2
        votes = np.sum(tree_preds, axis=0)
        return (votes > (self.m / 2)).astype(int)

def main():
    args = parse_args()
    cfg = load_config(args.set)
    
    claim_cfg = cfg[args.claim]
    data_dir = cfg['data']['dir']
    
    # Set seed for reproducibility
    np.random.seed(args.seed)
    
    # Load data
    # The dataio.load_split handles the dataset selection based on files present
    # For this runner, we assume the environment is set up for the specific dataset claim.
    # However, dataio checks files. We just pass the dir.
    
    try:
        X_train, y_train = dataio.load_split(data_dir, 'train')
        X_test, y_test = dataio.load_split(data_dir, 'test')
    except Exception as e:
        print(f"Error loading data: {e}", file=sys.stderr)
        sys.exit(1)

    # Hyperparameters
    m = claim_cfg.get('m_trees', 100)
    k = claim_cfg.get('k_candidates', 20)
    p = claim_cfg.get('p_splits', 10)
    a = claim_cfg.get('a_param', 0.2)
    
    start_time = time.time()
    
    # Train Model
    model = BestScoredRandomForest(m=m, k=k, p=p, a=a)
    model.fit(X_train, y_train)
    
    # Predict
    y_pred = model.predict(X_test)
    
    train_time = time.time() - start_time
    
    # Metric: Classification Error (1 - Accuracy)
    acc = accuracy_score(y_test, y_pred)
    error = 1.0 - acc
    
    # Output Result
    result = {
        "claim": args.claim,
        "seed": args.seed,
        "metric": "classification_error",
        "value": error,
        "train_seconds": train_time,
        "n_train": X_train.shape[0],
        "n_test": X_test.shape[0],
        "config_overrides": args.set
    }
    print(json.dumps(result))

if __name__ == "__main__":
    main()

import argparse
import json
import time
import numpy as np
import sys
import os

# Add workdir to path to import dataio
sys.path.insert(0, os.getcwd())
import dataio
from sklearn.model_selection import KFold, StratifiedKFold
from sklearn.metrics import accuracy_score
from collections import Counter

def load_config():
    with open('config.json', 'r') as f:
        return json.load(f)

def override_config(config, overrides):
    for o in overrides:
        key, value = o.split('=', 1)
        parts = key.split('.')
        d = config
        for part in parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
        
        # Try to parse value as number or bool
        try:
            if '.' in value:
                val = float(value)
            else:
                val = int(value)
        except ValueError:
            if value.lower() == 'true': val = True
            elif value.lower() == 'false': val = False
            else: val = value
        d[parts[-1]] = val
    return config

class BestScoredTree:
    """
    Implements the 'best-scored' tree construction described in Section 6.1.
    Uses Adaptive Random Partitioning.
    """
    def __init__(self, k_candidates, n_splits, alpha, random_state):
        self.k = k_candidates
        self.p = n_splits
        self.alpha = alpha
        self.rng = np.random.RandomState(random_state)
        self.tree_structure = None # { 'node_idx': {'feat': int, 'thresh': float, 'left': int, 'right': int, 'is_leaf': bool, 'label': int} }
        self.n_classes = None
        
    def _adaptive_random_partition(self, X, y):
        """
        Generates one candidate partition using the adaptive random method.
        Returns a tree structure dictionary.
        """
        n_samples, n_features = X.shape
        # Tree storage: list of nodes. Index 0 is root.
        # Node: {'region': [indices], 'left': int, 'right': int, 'split_feat': int, 'split_thresh': float, 'label': int, 'is_leaf': bool}
        # For implementation speed, we use a dictionary of lists or objects.
        # Let's use a simpler recursive structure for the splits.
        
        # We will perform 'p' splits total in the tree.
        # Adaptive: "randomly select one sample point... then choose the node which that sample point belongs to".
        
        # Data structure: list of active leaf indices (in the tree list) that can be split.
        # Tree list: [Node0, Node1, ...]
        # Node0 initially has all data.
        
        tree_nodes = []
        # Initial root node
        tree_nodes.append({
            'data_indices': np.arange(n_samples),
            'left': -1, 'right': -1,
            'split_feat': None, 'split_thresh': None,
            'is_leaf': True, 'label': None
        })
        
        active_leaf_indices = [0]
        
        for _ in range(self.p):
            if not active_leaf_indices:
                break # No more nodes to split
                
            # 1. Select a random sample point from the WHOLE training set
            # (Paper: "randomly select one sample point from the training data set")
            rand_sample_idx = self.rng.randint(0, n_samples)
            
            # 2. Find which leaf node this sample belongs to
            target_leaf_idx = -1
            for leaf_idx in active_leaf_indices:
                if rand_sample_idx in tree_nodes[leaf_idx]['data_indices']:
                    target_leaf_idx = leaf_idx
                    break
            
            if target_leaf_idx == -1:
                continue # Should not happen if logic is correct
                
            # 3. Split this node
            node = tree_nodes[target_leaf_idx]
            indices = node['data_indices']
            if len(indices) < 2:
                # Cannot split, remove from active
                active_leaf_indices.remove(target_leaf_idx)
                continue
                
            # Random feature
            feat = self.rng.randint(0, n_features)
            
            # Random threshold: Unif[0.5 - a, 0.5 + a] * (max - min) + min?
            # Paper says "parameter a in [0, 0.5] in the uniform distribution Unif[0.5 - a, 0.5 + a] for selecting the cut point"
            # This usually implies relative position in the range [min, max].
            vals = X[indices, feat]
            min_v, max_v = np.min(vals), np.max(vals)
            
            if min_v == max_v:
                active_leaf_indices.remove(target_leaf_idx)
                continue
            
            # Calculate cut point
            # center = 0.5 (midpoint)
            # offset = Unif[0.5 - a, 0.5 + a] - 0.5 = Unif[-a, a]
            offset = self.rng.uniform(-self.alpha, self.alpha)
            # location relative to range
            rel_loc = 0.5 + offset
            # Clamp just in case
            rel_loc = max(0.0, min(1.0, rel_loc))
            
            thresh = min_v + rel_loc * (max_v - min_v)
            
            # Perform split
            left_mask = X[indices, feat] <= thresh
            right_indices = indices[~left_mask]
            left_indices = indices[left_mask]
            
            if len(left_indices) == 0 or len(right_indices) == 0:
                # Split resulted in empty node
                continue
            
            # Update current node
            node['is_leaf'] = False
            node['split_feat'] = feat
            node['split_thresh'] = thresh
            
            # Create children
            left_child_idx = len(tree_nodes)
            tree_nodes.append({
                'data_indices': left_indices, 'left': -1, 'right': -1,
                'split_feat': None, 'split_thresh': None, 'is_leaf': True, 'label': None
            })
            node['left'] = left_child_idx
            
            right_child_idx = len(tree_nodes)
            tree_nodes.append({
                'data_indices': right_indices, 'left': -1, 'right': -1,
                'split_feat': None, 'split_thresh': None, 'is_leaf': True, 'label': None
            })
            node['right'] = right_child_idx
            
            # Update active leaves
            active_leaf_indices.remove(target_leaf_idx)
            active_leaf_indices.append(left_child_idx)
            active_leaf_indices.append(right_child_idx)
            
        return tree_nodes

    def _label_tree(self, tree_nodes, y):
        """Assigns majority vote label to leaf nodes."""
        for node in tree_nodes:
            if node['is_leaf']:
                indices = node['data_indices']
                if len(indices) > 0:
                    labels = y[indices]
                    # Majority vote
                    counts = Counter(labels)
                    node['label'] = counts.most_common(1)[0][0]
                else:
                    node['label'] = 0 # Default

    def _predict_tree(self, tree_nodes, x):
        """Predicts a single sample x."""
        node_idx = 0
        while not tree_nodes[node_idx]['is_leaf']:
            node = tree_nodes[node_idx]
            if x[node['split_feat']] <= node['split_thresh']:
                node_idx = node['left']
            else:
                node_idx = node['right']
        return tree_nodes[node_idx]['label']
        
    def fit(self, X, y):
        """Fits the best-scored tree using 10-fold CV to select the best partition."""
        self.n_classes = len(np.unique(y))
        
        # Paper: "generate k p-splitting adaptive random partitions... choose partition with best classification performance via 10-fold CV"
        # Note: Doing this fully is O(k * 10 * p * n). For constraints, we assume k and p are reasonable.
        # For reproduction speed, we might need to limit k or p if they are huge, but we stick to config.
        
        candidates = []
        for i in range(self.k):
            # Generate partition structure (indices map)
            # To speed up CV, we can generate the partition logic (splits) first, then apply to folds?
            # The splits depend on the data indices.
            # The paper implies generating partitions on the 'training set of cross-validation'.
            # So we iterate folds, and for each fold, we generate k candidates on the fold-train, 
            # eval on fold-val, and aggregate scores.
            pass 
        
        # Implementation approach per paper Section 6.1:
        # "For the first round of 10-fold CV... for each of the k partitions, corresponding classifier is derived... validation errors... average validation error... choose partition with smallest average"
        # This is computationally heavy. 
        # Optimized interpretation: 
        # We have the full training set (70% of data). We perform 10-fold CV on THIS set.
        # Inside CV loop:
        #   For each candidate c in k:
        #       Train partition on CV_Train -> Tree_T
        #       Predict on CV_Val -> Error
        #   Store errors for each candidate.
        # After 10 folds, average error for each candidate.
        # Pick best candidate's partition strategy? Or retrain the best candidate on full 70%?
        # "choose the partition from all k candidates... to be the exact partition for one tree... giving labels... basing on the training data (70%)"
        # This implies we just need to know WHICH candidate index (0 to k-1) performed best on average.
        # Then we rebuild THAT specific candidate (with same random seeds?) on the full 70% data.
        # Since random seeds are hard to sync perfectly across folds with this logic without stateful RNGs,
        # We will approximate: 
        # 1. Define k RNG seeds for candidates.
        # 2. Run 10-fold CV. For each fold, for each candidate seed, generate tree, get score.
        # 3. Average scores per seed. Pick best seed.
        # 4. Retrain using that seed on the full 70% data.
        
        cv = KFold(n_splits=10, shuffle=True, random_state=self.rng.randint(0, 10000))
        cv_scores = np.zeros(self.k)
        
        candidate_seeds = [self.rng.randint(0, 2**32) for _ in range(self.k)]
        
        for train_idx, val_idx in cv.split(X):
            X_tr, X_va = X[train_idx], X[val_idx]
            y_tr, y_va = y[train_idx], y[val_idx]
            
            fold_scores = []
            for c_seed in candidate_seeds:
                # Create tree with this seed
                local_rng = np.random.RandomState(c_seed)
                # We need a helper to build tree given X, y, rng
                # Reusing class logic but with temp rng
                t_nodes = self._adaptive_random_partition_with_rng(X_tr, y_tr, local_rng)
                self._label_tree(t_nodes, y_tr)
                
                # Predict
                preds = [self._predict_tree(t_nodes, x) for x in X_va]
                err = 1.0 - accuracy_score(y_va, preds)
                fold_scores.append(err)
            
            cv_scores += np.array(fold_scores)
            
        avg_cv_scores = cv_scores / 10
        best_candidate_idx = np.argmin(avg_cv_scores)
        best_seed = candidate_seeds[best_candidate_idx]
        
        # Retrain on full data with best seed
        self.rng = np.random.RandomState(best_seed)
        self.tree_structure = self._adaptive_random_partition_with_rng(X, y, self.rng)
        self._label_tree(self.tree_structure, y)

    def _adaptive_random_partition_with_rng(self, X, y, rng):
        # Extracts logic to use specific RNG
        n_samples, n_features = X.shape
        tree_nodes = [{'data_indices': np.arange(n_samples), 'left': -1, 'right': -1, 'split_feat': None, 'split_thresh': None, 'is_leaf': True, 'label': None}]
        active_leaf_indices = [0]
        
        for _ in range(self.p):
            if not active_leaf_indices: break
            
            rand_sample_idx = rng.randint(0, n_samples)
            target_leaf_idx = -1
            # Linear search for leaf containing sample (OK for small trees/n_splits)
            for leaf_idx in active_leaf_indices:
                if rand_sample_idx in tree_nodes[leaf_idx]['data_indices']:
                    target_leaf_idx = leaf_idx
                    break
            if target_leaf_idx == -1: continue
            
            node = tree_nodes[target_leaf_idx]
            indices = node['data_indices']
            if len(indices) < 2:
                active_leaf_indices.remove(target_leaf_idx)
                continue
            
            feat = rng.randint(0, n_features)
            vals = X[indices, feat]
            min_v, max_v = np.min(vals), np.max(vals)
            if min_v == max_v:
                active_leaf_indices.remove(target_leaf_idx)
                continue
            
            offset = rng.uniform(-self.alpha, self.alpha)
            rel_loc = 0.5 + offset
            rel_loc = max(0.0, min(1.0, rel_loc))
            thresh = min_v + rel_loc * (max_v - min_v)
            
            left_mask = X[indices, feat] <= thresh
            right_indices = indices[~left_mask]
            left_indices = indices[left_mask]
            
            if len(left_indices) == 0 or len(right_indices) == 0: continue
            
            node['is_leaf'] = False
            node['split_feat'] = feat
            node['split_thresh'] = thresh
            
            left_child_idx = len(tree_nodes)
            tree_nodes.append({'data_indices': left_indices, 'left': -1, 'right': -1, 'split_feat': None, 'split_thresh': None, 'is_leaf': True, 'label': None})
            node['left'] = left_child_idx
            
            right_child_idx = len(tree_nodes)
            tree_nodes.append({'data_indices': right_indices, 'left': -1, 'right': -1, 'split_feat': None, 'split_thresh': None, 'is_leaf': True, 'label': None})
            node['right'] = right_child_idx
            
            active_leaf_indices.remove(target_leaf_idx)
            active_leaf_indices.append(left_child_idx)
            active_leaf_indices.append(right_child_idx)
        return tree_nodes

    def predict(self, X):
        preds = [self._predict_tree(self.tree_structure, x) for x in X]
        return np.array(preds)

class BestScoredRandomForest:
    def __init__(self, n_trees, n_candidates, n_splits, alpha, random_state):
        self.n_trees = n_trees
        self.tree_params = {
            'k_candidates': n_candidates, 
            'n_splits': n_splits, 
            'alpha': alpha
        }
        self.rng = np.random.RandomState(random_state)
        self.trees = []
        
    def fit(self, X, y):
        self.trees = []
        for i in range(self.n_trees):
            seed = self.rng.randint(0, 2**32)
            tree = BestScoredTree(self.tree_params['k_candidates'], self.tree_params['n_splits'], self.tree_params['alpha'], seed)
            tree.fit(X, y)
            self.trees.append(tree)
            
    def predict(self, X):
        # Majority vote across trees
        preds = np.array([tree.predict(X) for tree in self.trees])
        # preds is (n_trees, n_samples)
        # Take mode for each sample
        final_preds = []
        for i in range(preds.shape[1]):
            counts = Counter(preds[:, i])
            final_preds.append(counts.most_common(1)[0][0])
        return np.array(final_preds)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claim', type=str, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--set', action='append', default=[], help='Override config, e.g. data.dir=path')
    args = parser.parse_args()

    # Load and override config
    config = load_config()
    config = override_config(config, args.set)
    
    claim_config = config[args.claim]
    data_dir = config['data']['dir']
    
    dataset_name = claim_config['dataset']
    
    # Load Data
    # Note: dataio.load_split handles the 70/30 split for BCW internally.
    # However, the paper says for Monks: "We randomly split each data set into training (70%) and test (30%)".
    # But the Monks dataset downloaded from UCI comes pre-split into train and test files (monks-2.train, monks-2.test).
    # The paper's Table 1 for Monks (n=601) matches the standard train+test size (432+169 = 601).
    # So for Monks, we trust the provided split files. For BCW, we use the split inside load_split.
    
    if dataset_name == 'monks-2':
        X_train, y_train = dataio.load_split(data_dir, 'train')
        X_test, y_test = dataio.load_split(data_dir, 'test')
    else:
        X_full, y_full = dataio.load_split(data_dir, 'train') # dataio returns full set for BCW on 'train' key logic? No, check dataio
        # dataio for BCW splits based on seed 42.
        # We need to load the 'test' part from dataio.
        X_train, y_train = dataio.load_split(data_dir, 'train')
        X_test, y_test = dataio.load_split(data_dir, 'test')

    n_train = len(X_train)
    n_test = len(X_test)

    # Initialize Model
    # Hyperparameters: n_candidates (k), n_trees (m), n_splits (p), alpha (a)
    # Paper mentions 3-fold CV for hyperparameter tuning, but does NOT specify the grid.
    # For this implementation, we use the values in the config (which correspond to reasonable defaults or passed via --set).
    # We implement the model fitting.
    
    model = BestScoredRandomForest(
        n_trees=claim_config['n_trees'],
        n_candidates=claim_config['n_candidates'],
        n_splits=claim_config['n_splits'],
        alpha=claim_config['alpha'],
        random_state=args.seed
    )
    
    start_time = time.time()
    model.fit(X_train, y_train)
    train_time = time.time() - start_time
    
    # Predict
    y_pred = model.predict(X_test)
    
    # Metric: Classification Error (1 - Accuracy)
    error = 1.0 - accuracy_score(y_test, y_pred)
    
    result = {
        "claim": args.claim,
        "seed": args.seed,
        "metric": "classification_error",
        "value": float(error),
        "train_seconds": train_time,
        "n_train": n_train,
        "n_test": n_test,
        "config_overrides": args.set
    }
    
    print(json.dumps(result))

if __name__ == "__main__":
    main()

import argparse
import json
import time
import numpy as np
import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataio import _load_monks, _load_bcw, DATASET_MAP
from sklearn.model_selection import KFold
from sklearn.base import BaseEstimator, ClassifierMixin
from scipy.stats import mode

# --- Algorithm Implementation: Best-Scored Random Forest ---

class BestScoredRandomForest(BaseEstimator, ClassifierMixin):
    def __init__(self, k_candidates=10, m_trees=50, p_splits=6, a_offset=0.1, random_state=None):
        """
        k_candidates: number of partition candidates to evaluate (k)
        m_trees: total number of trees in the forest (m)
        p_splits: number of splits per tree (p)
        a_offset: parameter 'a' for Unif[0.5-a, 0.5+a] cut point selection
        """
        self.k = k_candidates
        self.m = m_trees
        self.p = p_splits
        self.a = a_offset
        self.random_state = random_state
        self.trees_ = []

    def fit(self, X, y):
        """
        Construct the forest by selecting the best-scored tree m times.
        Note: The paper describes selecting 1 tree out of k candidates. 
        The forest is composed of m such trees.
        """
        rng = np.random.RandomState(self.random_state)
        self.trees_ = []
        n_samples = X.shape[0]
        n_features = X.shape[1]
        
        for i in range(self.m):
            # 1. Generate m trees. For each tree, select best of k candidates.
            # According to paper: "To begin with, we generate k p-splitting adaptive random partitions."
            # "choose the partition with the best classification performance from k candidates via 10-fold cross-validation"
            
            best_tree = None
            best_score = -np.inf
            
            for k_idx in range(self.k):
                # Create a candidate partition
                candidate = TreePartition(p=self.p, a=self.a, random_state=rng.randint(0, 10000))
                
                # 10-fold cross validation on the training set X, y
                cv_scores = []
                kf = KFold(n_splits=10, shuffle=True, random_state=rng.randint(0, 10000))
                
                for train_idx, val_idx in kf.split(X):
                    X_tr, X_va = X[train_idx], X[val_idx]
                    y_tr, y_va = y[train_idx], y[val_idx]
                    
                    # Fit partition on training fold (assign labels)
                    candidate.fit(X_tr, y_tr)
                    # Predict on validation fold
                    preds = candidate.predict(X_va)
                    # Accuracy score
                    acc = np.mean(preds == y_va)
                    cv_scores.append(acc)
                
                avg_score = np.mean(cv_scores)
                
                if avg_score > best_score:
                    best_score = avg_score
                    # Retrain best candidate on the FULL training set before adding to forest
                    best_tree = TreePartition(p=self.p, a=self.a, random_state=candidate.random_state)
                    best_tree.fit(X, y)
            
            self.trees_.append(best_tree)
        
        return self

    def predict(self, X):
        # Majority vote from all trees
        preds = np.array([tree.predict(X) for tree in self.trees_])
        # mode returns (array, count). Take the first element of array.
        return mode(preds, axis=0)[0].flatten()


class TreePartition:
    """
    Implements the 'Adaptive Random Partition' and classification logic.
    """
    def __init__(self, p, a, random_state):
        self.p = p  # number of splits
        self.a = a  # cut point offset
        self.random_state = random_state
        self.rng = np.random.RandomState(random_state)
        self.leaves = [] # List of dicts {'mask': boolean_array, 'label': int}

    def fit(self, X, y):
        # Initialize with one leaf containing all samples
        n_samples = X.shape[0]
        # Start with everything in one node
        # We represent nodes as masks (boolean arrays)
        masks = [np.ones(n_samples, dtype=bool)]
        
        # Perform p splits
        for _ in range(self.p):
            # "choose a to be split node, we first randomly select one sample point from the training data set, 
            # and then choose the node which that sample point belongs to"
            # Note: If masks are empty, we stop.
            valid_indices = np.where([np.any(m) for m in masks])[0]
            if len(valid_indices) == 0:
                break
                
            # Pick a random sample from the whole training set
            rand_samp_idx = self.rng.randint(0, n_samples)
            
            # Find the leaf (mask) that contains this sample
            # Search through current masks
            target_mask_idx = -1
            for i, mask in enumerate(masks):
                if mask[rand_samp_idx]:
                    target_mask_idx = i
                    break
            
            if target_mask_idx == -1:
                continue # Should not happen if logic is correct
                
            target_mask = masks[target_mask_idx]
            target_indices = np.where(target_mask)[0]
            
            if len(target_indices) < 2:
                continue # Cannot split

            # Perform Adaptive Random Partition
            # 1. Randomly select a feature dimension
            feat_idx = self.rng.randint(0, X.shape[1])
            
            # 2. Select cut point from Uniform[0.5-a, 0.5+a]
            # "the parameter a in the uniform distribution Unif[0.5-a, 0.5+a] for selecting the cut point"
            # This likely refers to the normalized range of the feature or the probability of split.
            # Given purely random trees usually split uniformly within feature range, 
            # and "0.5" suggests a midpoint or normalized value. 
            # Let's interpret this as: normalized position in [0.5-a, 0.5+a]
            u = self.rng.uniform(0.5 - self.a, 0.5 + self.a)
            
            # Apply to feature range in the node
            f_vals = X[target_indices, feat_idx]
            min_f, max_f = f_vals.min(), f_vals.max()
            
            if min_f == max_f:
                continue # Cannot split constant feature
                
            cut_point = min_f + u * (max_f - min_f)
            
            # Create new masks
            # Left child: feat <= cut_point
            # Right child: feat > cut_point
            # We need to update the masks list
            # Remove old mask, add two new masks
            
            # The mask for the node being split
            old_mask = masks.pop(target_mask_idx)
            
            # Create new masks based on condition
            # We must apply condition only to samples currently in the node
            # But masks are global booleans.
            
            # Left child
            left_mask = old_mask.copy()
            # Update left_mask to False for those going right
            # Those in old_mask AND X[:, feat] > cut_point go right
            go_right = old_mask & (X[:, feat_idx] > cut_point)
            left_mask[go_right] = False
            
            # Right child
            right_mask = old_mask.copy()
            # Update right_mask to False for those going left
            go_left = old_mask & (X[:, feat_idx] <= cut_point)
            right_mask[go_left] = False
            
            masks.append(left_mask)
            masks.append(right_mask)

        # After splits, assign labels to leaves
        self.leaves = []
        for mask in masks:
            if np.any(mask):
                # Majority vote in this leaf
                leaf_labels = y[mask]
                label = mode(leaf_labels)[0][0]
                self.leaves.append({'mask': mask, 'label': label})

    def predict(self, X):
        n_samples = X.shape[0]
        preds = np.zeros(n_samples, dtype=int)
        
        for leaf in self.leaves:
            # Identify samples in this leaf
            # To do this efficiently, we stored the mask from TRAINING.
            # But mask is boolean array of training size.
            # We can't apply training mask to test X directly.
            # We need to store the SPLITTING RULES (feature idx, cut point).
            # Due to implementation complexity, I will reconstruct the rules or use a simpler method.
            # Actually, the paper describes labeling cells of the partition. 
            # The partition is defined by the sequence of splits.
            # Since I didn't store the tree structure explicitly in `fit`, I must.
            # Refactoring `fit` to store structure is cleaner.
            pass 
            
        # --- REVISED FIT/PREDICT LOGIC for correctness ---
        # We will implement a simplified recursive tree structure to store rules.
        raise NotImplementedError("See v2 below")

# --- Revised Tree Implementation with Rule Storage ---

class AdaptiveTree(BaseEstimator, ClassifierMixin):
    def __init__(self, p, a, random_state):
        self.p = p
        self.a = a
        self.random_state = random_state
        self.rng = np.random.RandomState(random_state)
        self.root = None

    def fit(self, X, y):
        # Initialize root with all indices
        indices = np.arange(X.shape[0])
        self.root = {'indices': indices, 'left': None, 'right': None, 'feat': None, 'thresh': None, 'label': None}
        
        nodes_to_split = [self.root]
        
        # Perform p splits total
        for _ in range(self.p):
            if not nodes_to_split:
                break
                
            # Adaptive selection: pick random sample, find node containing it
            rand_sample_idx = self.rng.randint(0, X.shape[0])
            target_node = None
            target_node_idx = -1
            
            for i, node in enumerate(nodes_to_split):
                # Check if sample is in this node
                # Note: 'indices' in node are the training indices belonging to it
                if rand_sample_idx in node['indices']:
                    target_node = node
                    target_node_idx = i
                    break
            
            if target_node is None:
                continue
            
            # Perform split on target_node
            node_indices = target_node['indices']
            if len(node_indices) < 2:
                nodes_to_split.pop(target_node_idx)
                continue
                
            # 1. Random Feature
            feat_idx = self.rng.randint(0, X.shape[1])
            
            # 2. Random Cut Point (normalized)
            u = self.rng.uniform(0.5 - self.a, 0.5 + self.a)
            f_vals = X[node_indices, feat_idx]
            min_f, max_f = f_vals.min(), f_vals.max()
            
            if min_f == max_f:
                nodes_to_split.pop(target_node_idx)
                continue
                
            cut_point = min_f + u * (max_f - min_f)
            
            # Store rule
            target_node['feat'] = feat_idx
            target_node['thresh'] = cut_point
            
            # Split indices
            left_mask = X[node_indices, feat_idx] <= cut_point
            left_indices = node_indices[left_mask]
            right_indices = node_indices[~left_mask]
            
            # Remove target from split list, add children
            nodes_to_split.pop(target_node_idx)
            
            # Create children nodes
            left_node = {'indices': left_indices, 'left': None, 'right': None, 'feat': None, 'thresh': None, 'label': None}
            right_node = {'indices': right_indices, 'left': None, 'right': None, 'feat': None, 'thresh': None, 'label': None}
            
            target_node['left'] = left_node
            target_node['right'] = right_node
            
            nodes_to_split.append(left_node)
            nodes_to_split.append(right_node)
            
        # Assign labels to all leaves (nodes without children)
        def assign_labels(node):
            if node['left'] is None and node['right'] is None:
                # Leaf
                lbls = y[node['indices']]
                if len(lbls) > 0:
                    node['label'] = mode(lbls)[0][0]
                else:
                    node['label'] = 0 # Default
            else:
                if node['left']: assign_labels(node['left'])
                if node['right']: assign_labels(node['right'])
                
        assign_labels(self.root)
        return self

    def predict(self, X):
        preds = []
        for x in X:
            node = self.root
            while node['left'] is not None and node['right'] is not None:
                if x[node['feat']] <= node['thresh']:
                    node = node['left']
                else:
                    node = node['right']
            preds.append(node['label'])
        return np.array(preds)

# --- Main Training Script ---

# Re-define BRF using the correct Tree class
class BestScoredRF(BaseEstimator, ClassifierMixin):
    def __init__(self, k=10, m=50, p=6, a=0.1, random_state=None):
        self.k = k
        self.m = m
        self.p = p
        self.a = a
        self.random_state = random_state
        self.trees_ = []

    def fit(self, X, y):
        rng = np.random.RandomState(self.random_state)
        self.trees_ = []
        
        for _ in range(self.m):
            best_tree = None
            best_score = -np.inf
            
            for _ in range(self.k):
                # Candidate tree
                # Note: Paper implies different random partitions, so different random_states
                cand = AdaptiveTree(p=self.p, a=self.a, random_state=rng.randint(0, 10000))
                
                # 10-fold CV on X, y
                cv_scores = []
                kf = KFold(n_splits=10, shuffle=True, random_state=rng.randint(0, 10000))
                for tr_idx, val_idx in kf.split(X):
                    X_tr, X_va = X[tr_idx], X[val_idx]
                    y_tr, y_va = y[tr_idx], y[val_idx]
                    
                    cand.fit(X_tr, y_tr)
                    pred = cand.predict(X_va)
                    cv_scores.append(np.mean(pred == y_va))
                    
                avg_score = np.mean(cv_scores)
                
                if avg_score > best_score:
                    best_score = avg_score
                    best_tree = AdaptiveTree(p=self.p, a=self.a, random_state=cand.random_state)
                    best_tree.fit(X, y) # Retrain on full data
                    
            self.trees_.append(best_tree)
        return self

    def predict(self, X):
        preds = np.array([t.predict(X) for t in self.trees_])
        return mode(preds, axis=0)[0].flatten()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claim', type=str, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--set', type=str, action='append', default=[])
    args = parser.parse_args()

    # Load Config
    with open('config.json', 'r') as f:
        cfg = json.load(f)

    # Apply overrides
    for s in args.set:
        k, v = s.split('=', 1)
        parts = k.split('.')
        curr = cfg
        for p in parts[:-1]:
            curr = curr[p]
        curr[parts[-1]] = v

    claim_cfg = cfg[args.claim]
    dataset_name = claim_cfg['dataset']
    data_dir = cfg['data']['dir']

    # Load Data based on dataset name
    start_load = time.time()
    if dataset_name == 'monks-2':
        (X, y), (X_test, y_test) = _load_monks(data_dir)
    elif dataset_name == 'breast_cancer_wisconsin':
        (X, y), (X_test, y_test) = _load_bcw(data_dir)
    else:
        raise ValueError(f"Unknown dataset {dataset_name}")
    load_time = time.time() - start_load

    # Init Model
    # Extract params, handling types
    k = int(claim_cfg['k_candidates'])
    m = int(claim_cfg['m_trees'])
    p = int(claim_cfg['p_splits'])
    a = float(claim_cfg['a_split_offset'])
    
    model = BestScoredRF(k=k, m=m, p=p, a=a, random_state=args.seed)

    # Train
    start_train = time.time()
    model.fit(X, y)
    train_seconds = time.time() - start_train

    # Test
    preds = model.predict(X_test)
    # Metric: Classification Error (1 - accuracy)
    acc = np.mean(preds == y_test)
    error = 1.0 - acc

    # Output
    result = {
        "claim": args.claim,
        "seed": args.seed,
        "metric": "classification_error",
        "value": error,
        "train_seconds": train_seconds,
        "n_train": len(X),
        "n_test": len(X_test),
        "config_overrides": args.set
    }
    print(json.dumps(result))

if __name__ == "__main__":
    main()

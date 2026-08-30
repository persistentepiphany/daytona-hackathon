import argparse
import json
import time
import numpy as np
import os
import sys

# Import data loading
import dataio

# Sklearn imports
from sklearn.model_selection import train_test_split, KFold
from sklearn.metrics import accuracy_score

def parse_overrides(overrides):
    """Parse --set key=value arguments into a dict."""
    config = {}
    for s in overrides:
        if '=' in s:
            k, v = s.split('=', 1)
            # Try to parse as number
            try:
                if '.' in v:
                    v = float(v)
                else:
                    v = int(v)
            except ValueError:
                pass # keep as string
            keys = k.split('.')
            d = config
            for key_part in keys[:-1]:
                if key_part not in d:
                    d[key_part] = {}
                d = d[key_part]
            d[keys[-1]] = v
    return config

def get_config(claim_id, overrides):
    # Load base config
    with open('config.json', 'r') as f:
        base_cfg = json.load(f)
    
    # Get claim specific defaults
    claim_cfg = base_cfg.get(claim_id, {})
    
    # Apply overrides
    override_cfg = parse_overrides(overrides)
    
    # Merge simple dicts (overwrites)
    def merge(d1, d2):
        for k, v in d2.items():
            if k in d1 and isinstance(d1[k], dict) and isinstance(v, dict):
                merge(d1[k], v)
            else:
                d1[k] = v
        return d1

    final_cfg = merge(claim_cfg.copy(), override_cfg)
    # Add data dir if not present
    if 'data' not in final_cfg:
        final_cfg['data'] = base_cfg.get('data', {'dir': 'localdata'})
    else:
        if 'dir' not in final_cfg['data']:
             final_cfg['data']['dir'] = base_cfg.get('data', {}).get('dir', 'localdata')
             
    return final_cfg

class BestScoredTree:
    def __init__(self, n_candidates=5, max_splits=10, a_param=0.1):
        self.n_candidates = n_candidates
        self.max_splits = max_splits
        self.a_param = a_param
        self.tree_structure = None # Will hold the chosen partition
        self.classes_ = None
        self.n_features_ = None

    def _create_partition(self, X, y, rng):
        """
        Creates one adaptive random partition tree candidate.
        Returns a function that can predict labels for X.
        """
        n_samples, n_features = X.shape
        
        # Tree nodes: list of dicts. Index 0 is root.
        # Each node: {'feature': f, 'threshold': t, 'left': idx, 'right': idx, 'leaf': bool, 'label': val}
        # We implement a recursive grower but adaptively select nodes.
        
        nodes = []
        
        # Helper to find leaf node for a sample
        def get_leaf_idx(sample, nodes):
            idx = 0
            while True:
                node = nodes[idx]
                if node['leaf']:
                    return idx
                if sample[node['feature']] <= node['threshold']:
                    idx = node['left']
                else:
                    idx = node['right']
        
        # Start with root node containing all samples (conceptually)
        # To implement adaptive splitting: randomly select a sample, find its node, split it.
        # But we must construct the tree structure first.
        
        # Strategy: We simulate the process. 
        # We maintain a list of active leaf indices.
        # For each split step up to max_splits:
        # 1. Pick a random sample from X_train.
        # 2. Traverse current tree to find which leaf it falls into.
        # 3. If that leaf has > 1 sample (and maybe other conditions), split it.
        
        # Node structure: [is_leaf, left_child_idx, right_child_idx, feat, thresh, label]
        # Initialize root as leaf
        nodes.append({'leaf': True, 'label': None, 'samples': list(range(n_samples))})
        
        active_leaves = [0] # indices of leaves in nodes list
        
        # We need to track which samples are in which leaf efficiently.
        # For small dataset or smoke test, simple iteration is fine.
        
        current_splits = 0
        while current_splits < self.max_splits and len(active_leaves) > 0:
            # 1. Randomly select a sample point
            sample_idx = rng.randint(0, n_samples)
            
            # 2. Find leaf containing this sample
            target_leaf_idx = -1
            for l_idx in active_leaves:
                if sample_idx in nodes[l_idx]['samples']:
                    target_leaf_idx = l_idx
                    break
            
            if target_leaf_idx == -1:
                continue # Should not happen
            
            # Check if we can split (e.g. > 1 sample, not pure)
            leaf_samples = nodes[target_leaf_idx]['samples']
            if len(leaf_samples) <= 1:
                continue
            
            # Check purity
            leaf_labels = y[leaf_samples]
            if len(np.unique(leaf_labels)) == 1:
                continue

            # 3. Perform Split
            # Random feature
            feat = rng.randint(0, n_features)
            
            # Adaptive/Random Threshold
            # Paper: Unif[0.5 - a, 0.5 + a] relative to min/max? 
            # Usually purely random is uniform between min and max of feature in node.
            # The 'adaptive' part refers to NODE selection. The split point is still random.
            # Let's use standard uniform random between min and max of the feature in this node.
            feat_vals = X[leaf_samples, feat]
            min_v, max_v = np.min(feat_vals), np.max(feat_vals)
            if min_v == max_v:
                continue # Cannot split
                
            # Threshold: random uniform between min and max.
            # The paper mentions parameter 'a' for Unif[0.5-a, 0.5+a]. 
            # This might be for selecting a quantile? Or normalized cut point?
            # "parameter a in the uniform distribution Unif[0.5-a, 0.5+a] for selecting the cut point"
            # If feature is normalized [0,1], this makes sense. Let's assume normalized or scale it.
            # Given we don't normalize, we interpret this as:
            # Split at p-th quantile where p ~ Unif[0.5-a, 0.5+a].
            p = rng.uniform(0.5 - self.a_param, 0.5 + self.a_param)
            p = np.clip(p, 0.0, 1.0) # Safety
            
            threshold = np.percentile(feat_vals, p * 100)
            
            # Safety to ensure split happens (move slightly away from min/max)
            if threshold <= min_v: threshold = min_v + (max_v-min_v)*0.01
            if threshold >= max_v: threshold = max_v - (max_v-min_v)*0.01

            # Create children
            left_samples = [i for i in leaf_samples if X[i, feat] <= threshold]
            right_samples = [i for i in leaf_samples if X[i, feat] > threshold]
            
            if len(left_samples) == 0 or len(right_samples) == 0:
                continue
            
            # Update Tree
            nodes[target_leaf_idx]['leaf'] = False
            nodes[target_leaf_idx]['feature'] = feat
            nodes[target_leaf_idx]['threshold'] = threshold
            nodes[target_leaf_idx]['left'] = len(nodes)
            nodes[target_leaf_idx]['right'] = len(nodes) + 1
            del nodes[target_leaf_idx]['samples'] # No longer needed
            
            # Add child nodes
            nodes.append({'leaf': True, 'label': None, 'samples': left_samples})
            nodes.append({'leaf': True, 'label': None, 'samples': right_samples})
            
            # Update active leaves
            active_leaves.remove(target_leaf_idx)
            active_leaves.append(len(nodes) - 2)
            active_leaves.append(len(nodes) - 1)
            
            current_splits += 1
            
        # Assign labels to remaining leaves based on training set (passed later)
        # This structure is just the geometry. We return it.
        return nodes

    def fit(self, X, y):
        self.classes_ = np.unique(y)
        self.n_features_ = X.shape[1]
        
        # 1. Generate k candidates using 10-fold CV on Training set (X, y)
        # Note: The paper says "Based on the training set of cross-validation... choose partition with best avg validation error".
        # We generate k partitions. For each partition, we evaluate via 10-fold CV.
        
        kf = KFold(n_splits=10, shuffle=True, random_state=42) # Fixed seed for CV structure consistency
        
        best_candidate = None
        best_avg_error = float('inf')
        
        rng = np.random.RandomState(0) # Local RNG for partition construction
        
        for i in range(self.n_candidates):
            # Generate a partition structure (geometry)
            # Structure depends only on random choices in _create_partition
            # To evaluate it fairly, we need to train it (assign labels) on CV-train, test on CV-val.
            # BUT, the partition geometry itself might depend on data if we use data to pick splits.
            # "Purely random" usually means splits are independent of Y. "Adaptive" node selection depends on X density.
            # If geometry depends on X, we must refit geometry for each CV fold? 
            # Paper: "generate k p-splitting adaptive random partitions... traverse all ten rounds... choose partition with smallest avg validation error".
            # This implies the *geometry* is fixed for the candidate, and we just test how well it performs across folds.
            # If geometry depends on X, we should generate it on the full X before splitting?
            # Or generate it inside the fold? 
            # "Adaptive" partition: selects node based on sample point. This uses X.
            # So for strict CV, we should generate the partition on the training part of the CV fold.
            # However, the text says "choose THE partition from all k candidates". Singular.
            # Let's assume we generate the partition geometry on the full (X,y) provided to `fit`, then evaluate it via CV.
            # This is slightly optimistic but matches "select the one with best empirical performance... as each single tree".
            
            partition_nodes = self._create_partition(X, y, rng)
            
            # Evaluate this partition via 10-fold CV
            fold_errors = []
            for train_idx, val_idx in kf.split(X):
                X_tr, X_val = X[train_idx], X[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]
                
                # Assign labels to leaves based on y_tr
                # We need a predict function that uses this geometry
                def predict_partition(X_pred, nodes, y_labels_source):
                    # Assign labels to nodes
                    labeled_nodes = []
                    # Deep copy simple structure (list of dicts)
                    for n in nodes:
                        new_n = n.copy()
                        if 'samples' in n:
                            # It's a leaf in the structure (might be internal in final tree, but we only split leaves)
                            # Wait, 'samples' key is only added during construction for leaf nodes.
                            # Internal nodes had 'samples' deleted.
                            if 'samples' in n:
                                labels = y_labels_source[n['samples']]
                                # Majority vote
                                counts = np.bincount(labels)
                                new_n['label'] = np.argmax(counts)
                        labeled_nodes.append(new_n)
                    
                    # Predict
                    preds = []
                    for sample in X_pred:
                        idx = 0
                        while not labeled_nodes[idx]['leaf']:
                            if sample[labeled_nodes[idx]['feature']] <= labeled_nodes[idx]['threshold']:
                                idx = labeled_nodes[idx]['left']
                            else:
                                idx = labeled_nodes[idx]['right']
                        preds.append(labeled_nodes[idx]['label'])
                    return np.array(preds)
                
                try:
                    y_pred = predict_partition(X_val, partition_nodes, y_tr)
                    err = 1.0 - accuracy_score(y_val, y_pred)
                    fold_errors.append(err)
                except:
                    fold_errors.append(1.0) # Max error on failure
            
            avg_err = np.mean(fold_errors)
            if avg_err < best_avg_error:
                best_avg_error = avg_err
                best_candidate = partition_nodes
        
        self.tree_structure = best_candidate
        
        # Finalize the tree: Assign labels based on the full training set (X, y)
        # We re-use the logic but for the final storage
        if self.tree_structure is None: return self
        
        for node in self.tree_structure:
            if 'samples' in node:
                labels = y[node['samples']]
                counts = np.bincount(labels, minlength=len(self.classes_)) # ensure all classes present
                node['label'] = np.argmax(counts)
                # Clean up samples to save space (optional)
                del node['samples']
        
        return self

    def predict(self, X):
        if self.tree_structure is None:
            raise Exception("Not fitted")
        
        preds = []
        for sample in X:
            idx = 0
            while not self.tree_structure[idx]['leaf']:
                if sample[self.tree_structure[idx]['feature']] <= self.tree_structure[idx]['threshold']:
                    idx = self.tree_structure[idx]['left']
                else:
                    idx = self.tree_structure[idx]['right']
            preds.append(self.tree_structure[idx]['label'])
        return np.array(preds)

class BestScoredForest:
    def __init__(self, n_trees=20, n_candidates=5, max_splits=10, a_param=0.1):
        self.n_trees = n_trees
        self.n_candidates = n_candidates
        self.max_splits = max_splits
        self.a_param = a_param
        self.trees = []
        self.classes_ = None

    def fit(self, X, y):
        self.classes_ = np.unique(y)
        self.trees = []
        for i in range(self.n_trees):
            tree = BestScoredTree(self.n_candidates, self.max_splits, self.a_param)
            tree.fit(X, y)
            self.trees.append(tree)
        return self

    def predict(self, X):
        # Aggregate votes
        if not self.trees:
            raise Exception("Not fitted")
        
        preds = np.array([t.predict(X) for t in self.trees])
        # Majority vote
        # For binary 0/1, sum > n_trees/2 means 1.
        # General case: mode
        y_pred = []
        for i in range(X.shape[0]):
            votes = preds[:, i]
            counts = np.bincount(votes, minlength=len(self.classes_))
            y_pred.append(np.argmax(counts))
        return np.array(y_pred)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claim', type=str, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--set', action='append', default=[], help='Override config key=value')
    args = parser.parse_args()

    config = get_config(args.claim, args.set)
    
    # Set random seeds
    np.random.seed(args.seed)
    
    # Load Data
    data_dir = config['data']['dir']
    X_all, y_all = dataio.load_split(data_dir, 'train') # 'train' here just means load the file
    
    # Split into Train/Test (70/30)
    # Paper: "randomly split... 70% training... 30% testing"
    # We repeat this for the number of repetitions specified, but train.py runs once.
    # We treat the single run as one repetition.
    test_size = config.get('test_size', 0.3)
    X_train, X_test, y_train, y_test = train_test_split(
        X_all, y_all, test_size=test_size, random_state=args.seed, stratify=y_all
    )
    
    # Initialize Model
    model = BestScoredForest(
        n_trees=config.get('n_trees', 20),
        n_candidates=config.get('n_candidates', 5),
        max_splits=config.get('max_splits', 15),
        a_param=config.get('a_param', 0.1)
    )
    
    # Train
    start_time = time.time()
    model.fit(X_train, y_train)
    train_time = time.time() - start_time
    
    # Predict
    y_pred = model.predict(X_test)
    
    # Metric: Classification Error (1 - Accuracy)
    acc = accuracy_score(y_test, y_pred)
    error = 1.0 - acc
    
    # Output
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

if __name__ == '__main__':
    main()

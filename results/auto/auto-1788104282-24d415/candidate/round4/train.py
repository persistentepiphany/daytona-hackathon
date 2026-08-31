import argparse
import json
import time
import numpy as np
import os
import sys
import warnings

# Ignore sklearn convergence warnings etc.
warnings.filterwarnings('ignore')

from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import zero_one_loss

# Configuration Loading
CONFIG_PATH = 'config.json'

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def update_config(config, overrides):
    """Update config with dotted key overrides."""
    for override in overrides:
        if '=' not in override:
            continue
        key, value = override.split('=', 1)
        parts = key.split('.')
        obj = config
        for part in parts[:-1]:
            if part not in obj:
                obj[part] = {}
            obj = obj[part]
        
        # Try to parse as JSON/number/bool
        try:
            obj[parts[-1]] = json.loads(value)
        except:
            obj[parts[-1]] = value
    return config

# --- Algorithm Implementation ---

class BestScoredRandomForest:
    def __init__(self, n_candidates, n_splits, a, m_trees, random_state=None):
        """
        n_candidates (k): number of candidate partitions per tree.
        n_splits (p): number of splits per tree (depth control).
        a: parameter for Unif[0.5-a, 0.5+a] cut point selection.
        m_trees: number of trees in forest.
        """
        self.k = n_candidates
        self.p = n_splits
        self.a = a
        self.m = m_trees
        self.random_state = random_state
        self.trees = [] # Stores (feature_idx, threshold, label_left, label_right) tuples for splits
        self.rng = np.random.RandomState(random_state)

    def fit(self, X, y):
        # Store training data for prediction (memory intensive but faithful to partition logic)
        self.X_train = X
        self.y_train = y
        self.trees = []
        
        n_samples, n_features = X.shape
        
        for _ in range(self.m):
            # 1. Generate k candidate partitions and select best via 10-fold CV
            best_tree = self._select_best_tree(X, y)
            self.trees.append(best_tree)
            
    def _select_best_tree(self, X, y):
        n_samples, n_features = X.shape
        
        candidates = []
        for i in range(self.k):
            # Build a candidate tree structure (partition)
            # We use a recursive definition or a set of splits.
            # For simplicity and speed in Python, we define a tree as a list of nodes.
            # A node is (f_idx, threshold, left_sub_idx, right_sub_idx, leaf_label).
            # Actually, purely random tree splits p times. This defines a region.
            # To evaluate efficiently, we can assign a leaf index to each sample.
            
            # Tree generation logic:
            # Select p splits.
            # Split feature: uniform random 0..n_features-1
            # Split threshold: Unif[0.5-a, 0.5+a]. 
            # Note: This interval is centered at 0.5. This assumes features are normalized to [0,1].
            # Monks data is one-hot (0 or 1). BCW data is integers 1-10.
            # This parameter 'a' is defined in the paper for normalized data.
            # Since our data isn't normalized to [0,1] exactly (though binary is), 
            # we apply the threshold relative to the feature range [min, max] or just raw.
            # Paper says "select the cut point... Unif[0.5-a, 0.5+a]". 
            # This strongly implies normalized features. We should normalize.
            # BUT, the tree selection is done via CV on the training set. 
            # The normalization parameters must be derived from the training fold.
            
            # To keep it simple and compliant with the "random" nature without scaling complications:
            # We will interpret the threshold relative to min/max of the feature in the dataset.
            # threshold = min + Unif[0.5-a, 0.5+a] * (max - min)
            
            tree_nodes = [] # List of (f_idx, threshold)
            for _ in range(self.p):
                f = self.rng.randint(0, n_features)
                vals = X[:, f]
                min_v, max_v = vals.min(), vals.max()
                if min_v == max_v:
                    t = min_v
                else:
                    u = self.rng.uniform(0.5 - self.a, 0.5 + self.a)
                    t = min_v + u * (max_v - min_v)
                tree_nodes.append((f, t))
            
            candidates.append(tree_nodes)
            
        # Evaluate candidates via 10-fold CV
        skf = StratifiedKFold(n_splits=10, shuffle=True, random_state=self.rng)
        best_score = np.inf
        best_candidate_idx = 0
        
        for idx, tree_nodes in enumerate(candidates):
            cv_scores = []
            for train_idx, val_idx in skf.split(X, y):
                X_tr, X_val = X[train_idx], X[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]
                
                # Determine labels for leaves based on X_tr, y_tr
                leaf_labels = self._get_leaf_labels(tree_nodes, X_tr, y_tr)
                
                # Predict on X_val
                pred = self._predict_with_tree(tree_nodes, X_val, leaf_labels)
                err = zero_one_loss(y_val, pred)
                cv_scores.append(err)
                
            avg_score = np.mean(cv_scores)
            if avg_score < best_score:
                best_score = avg_score
                best_candidate_idx = idx
                
        # Retrain the best candidate on the full (X, y)
        best_tree_nodes = candidates[best_candidate_idx]
        final_leaf_labels = self._get_leaf_labels(best_tree_nodes, X, y)
        return best_tree_nodes, final_leaf_labels

    def _get_leaf_labels(self, tree_nodes, X, y):
        """
        Assigns a label (0 or 1) to every possible leaf path defined by tree_nodes.
        Since the tree is fully constructed with p splits, there are 2^p leaves (though some empty).
        We simply simulate the traversal for all X and aggregate labels per leaf.
        
        Returns a dict: {leaf_id: label}
        """
        # We can approximate this by just returning the predictions for the training set
        # and using a nearest-neighbor or fallback, but the tree partitions the space.
        # Let's store the partition boundaries? Too complex.
        # The paper says: "labeling each cells ... according to the majority votes".
        # If we store the training samples indices in each leaf, we can vote at test time.
        
        leaf_indices = {} # leaf_id -> list of sample indices
        n_samples = X.shape[0]
        
        for i in range(n_samples):
            leaf_id = self._traverse(tree_nodes, X[i])
            if leaf_id not in leaf_indices:
                leaf_indices[leaf_id] = []
            leaf_indices[leaf_id].append(i)
            
        leaf_labels = {}
        for lid, indices in leaf_indices.items():
            if len(indices) == 0:
                leaf_labels[lid] = 0 # Default
            else:
                counts = np.bincount(y[indices].astype(int))
                leaf_labels[lid] = np.argmax(counts)
                
        return leaf_labels

    def _traverse(self, tree_nodes, x):
        """
        Given a sample x, find its leaf ID.
        ID is a bitmask of decisions: 0 for left, 1 for right.
        """
        node_id = 0
        for f, t in tree_nodes:
            if x[f] <= t:
                bit = 0
            else:
                bit = 1
            node_id = (node_id << 1) | bit
        return node_id

    def _predict_with_tree(self, tree_nodes, X, leaf_labels):
        preds = []
        for x in X:
            lid = self._traverse(tree_nodes, x)
            # Default to 0 if leaf not seen in training (unlikely with random splits on dense data)
            preds.append(leaf_labels.get(lid, 0))
        return np.array(preds)

    def predict(self, X):
        # Aggregate votes from all trees
        votes = np.zeros((X.shape[0], 2))
        for tree_nodes, leaf_labels in self.trees:
            preds = self._predict_with_tree(tree_nodes, X, leaf_labels)
            for i, p in enumerate(preds):
                votes[i, p] += 1
        return np.argmax(votes, axis=1)

# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claim', type=str, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--set', type=str, action='append', default=[], help='Override config keys, e.g., data.dir=value')
    args = parser.parse_args()

    config = load_config()
    config = update_config(config, args.set)
    
    claim_cfg = config[args.claim]
    dataset_name = claim_cfg['dataset']
    
    # Expose dataset name to dataio
    import dataio
    dataio.CURRENT_DATASET = dataset_name
    
    X, y = dataio.load_split(config['data']['dir'], 'train')
    X_test, y_test = dataio.load_split(config['data']['dir'], 'test')
    
    start_time = time.time()
    
    # Parameter extraction
    # Note: Paper mentions k (n_candidates), m (m_trees), p (n_splits), a.
    # It also mentions 3-fold CV for hyperparameter tuning. 
    # Since we only have one claim ID per dataset and no specific values in the config 
    # provided in the prompt (just IDs), we use the values in the config file we created.
    # The config file has reasonable defaults (k=10, m=50, p=10).
    # In a full reproduction, we would run an outer loop over params. 
    # Here we run the "best-scored RF" with the specified parameters.
    
    model = BestScoredRandomForest(
        n_candidates=claim_cfg.get('n_candidates', 10),
        n_splits=claim_cfg.get('n_splits', 10),
        a=claim_cfg.get('a', 0.5),
        m_trees=claim_cfg.get('m_trees', 50),
        random_state=args.seed
    )
    
    model.fit(X, y)
    
    predictions = model.predict(X_test)
    
    # Metric: Classification Error
    error = zero_one_loss(y_test, predictions)
    
    elapsed = time.time() - start_time
    
    result = {
        "claim": args.claim,
        "seed": args.seed,
        "metric": "classification_error",
        "value": float(error),
        "train_seconds": elapsed,
        "n_train": int(X.shape[0]),
        "n_test": int(X_test.shape[0]),
        "config_overrides": args.set
    }
    
    print(json.dumps(result))

if __name__ == '__main__':
    main()

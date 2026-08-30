import argparse
import json
import time
import numpy as np
import sys
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.utils.validation import check_X_y, check_array, check_is_fitted
from sklearn.utils.multiclass import unique_labels
from sklearn.model_selection import KFold
from sklearn.neighbors import NearestCentroid
from imblearn.over_sampling import RandomOverSampler
import dataio

# --- Best-Scored Random Tree Implementation ---

class BestScoredRandomTree(ClassifierMixin, BaseEstimator):
    def __init__(self, n_candidates=10, n_splits=5, alpha=0.1):
        self.n_candidates = n_candidates
        self.n_splits = n_splits
        self.alpha = alpha

    def fit(self, X, y):
        X, y = check_X_y(X, y)
        self.classes_ = unique_labels(y)
        self.n_features_in_ = X.shape[1]
        
        # Store training data for prediction (memory-based tree)
        self.X_ = X
        self.y_ = y
        
        # Adaptive Partition Generation & Selection via 10-Fold CV
        kf = KFold(n_splits=10, shuffle=True, random_state=42)
        best_candidate_idx = -1
        best_score = -np.inf
        candidate_trees = []
        
        # Generate candidates (these are the random partitions)
        for _ in range(self.n_candidates):
            # Generate random parameters for this candidate partition
            # Feature indices for p splits: randomly select one feature per split level
            # "every dimension must have the chance to be split"
            feat_idxs = np.random.randint(0, self.n_features_in_, size=self.n_splits)
            
            # Cut points: Uniform[0.5-a, 0.5+a] within feature range
            cuts = np.random.uniform(0.5 - self.alpha, 0.5 + self.alpha, size=self.n_splits)
            
            # Scale cuts to actual feature ranges: min + cut * (max - min)
            real_cuts = []
            for i in range(self.n_splits):
                f_idx = feat_idxs[i]
                f_min = X[:, f_idx].min()
                f_max = X[:, f_idx].max()
                span = f_max - f_min
                if span == 0: span = 1.0
                real_cuts.append(f_min + cuts[i] * span)
            
            candidate = {'features': feat_idxs, 'cuts': np.array(real_cuts)}
            
            # Evaluate candidate via CV
            cv_scores = []
            for train_idx, val_idx in kf.split(X):
                X_tr, X_va = X[train_idx], X[val_idx]
                y_tr, y_va = y[train_idx], y[val_idx]
                
                # Train: label leaves on X_tr
                # Predict on X_va
                preds = self._predict_partition(X_va, candidate, X_tr, y_tr)
                score = np.mean(preds == y_va)
                cv_scores.append(score)
            
            avg_score = np.mean(cv_scores)
            candidate_trees.append(candidate)
            
            if avg_score > best_score:
                best_score = avg_score
                best_candidate_idx = len(candidate_trees) - 1
        
        self.best_tree_ = candidate_trees[best_candidate_idx]
        return self

    def _predict_partition(self, X, tree, X_fit, y_fit):
        # X: samples to predict
        # tree: dict with features, cuts
        # X_fit, y_fit: data used to determine leaf labels
        
        n = X.shape[0]
        current_indices = np.arange(n)
        
        for depth in range(self.n_splits):
            if len(current_indices) == 0:
                break
            
            f_idx = tree['features'][depth]
            cut = tree['cuts'][depth]
            
            # Points in current set
            X_sub = X[current_indices]
            
            # Boolean mask for going right
            go_right = X_sub[:, f_idx] > cut
            
            # In paper: "right child (L_i, 1, s_i) if feature > cut"
            # We keep indices that go right. 
            # If they don't go right, they stay in the left leaf of this step?
            # No, tree structure: split current node.
            # But here we just follow the path. 
            # Simple implementation: filter indices that satisfy condition to proceed to next split.
            # Indices that fail the condition at any step stop there and become leaves.
            
            # Identify which of the current indices proceed
n            proceed_mask = go_right
            proceed_indices = current_indices[proceed_mask]
            
            # Identify which stop here (left child of this split)
            stop_indices = current_indices[~proceed_mask]
            
            # Store predictions for those stopping here
            # Need to find which leaf they fall into based on X_fit
            if len(stop_indices) > 0:
                X_stop = X[stop_indices]
                labels = self._get_labels(X_stop, tree, depth, X_fit, y_fit)
                # We need a way to return these. We'll use a temporary array.
                if not hasattr(self, '_temp_preds'):
                    self._temp_preds = np.full(n, -1)
                self._temp_preds[stop_indices] = labels
            
            # Continue with the right child
            current_indices = proceed_indices
            
        # Handle remaining indices (went right at all splits or path ended)
        if len(current_indices) > 0:
            X_rem = X[current_indices]
            labels = self._get_labels(X_rem, tree, self.n_splits, X_fit, y_fit)
            if not hasattr(self, '_temp_preds'):
                self._temp_preds = np.full(n, -1)
            self._temp_preds[current_indices] = labels
        
        return self._temp_preds

    def _get_labels(self, X_query, tree, depth, X_fit, y_fit):
        # Determine which leaf (defined by path up to 'depth') each X_query falls into
        # Then return majority vote of y_fit in that leaf
        
        # Start with all X_fit in the root
        mask = np.ones(len(X_fit), dtype=bool)
        
        for d in range(depth):
            f_idx = tree['features'][d]
            cut = tree['cuts'][d]
            
            # Filter X_fit to those that went right (same path)
            # Points in X_fit that satisfy > cut continue to next step
            # Points that don't are excluded (they went left)
            mask = mask & (X_fit[:, f_idx] > cut)
        
        y_leaf = y_fit[mask]
        if len(y_leaf) == 0:
            return 0 # Default class
        
        vals, counts = np.unique(y_leaf, return_counts=True)
        return vals[np.argmax(counts)]

    def predict(self, X):
        check_is_fitted(self)
        X = check_array(X)
        # Use stored X_, y_ as fitting data for leaf labeling
        preds = self._predict_partition(X, self.best_tree_, self.X_, self.y_)
        return preds.astype(int)

# --- Main Script ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claim', type=str, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--set', action='append', default=[])
    args = parser.parse_args()

    # Load Config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Apply overrides
    override_dict = {}
    for s in args.set:
        k, v = s.split('=', 1)
        override_dict[k] = v
    
    # Helper to get config value with override
    def get_cfg(path):
        keys = path.split('.')
        obj = config
        for k in keys:
            if isinstance(obj, dict) and k in obj:
                obj = obj[k]
            else:
                return override_dict.get(path, None)
        # Override takes precedence if provided for the specific path
        if path in override_dict:
            val = override_dict[path]
            # Try to parse numeric
            try:
                return int(val)
            except ValueError:
                try:
                    return float(val)
                except ValueError:
                    return val
        return obj

    claim_cfg = config[args.claim]
    dataset_name = claim_cfg['dataset']
    data_dir = get_cfg('data.dir') or 'localdata'
    
    # Hyperparameters
    k = get_cfg(f'{args.claim}.n_candidates')
    m = get_cfg(f'{args.claim}.n_trees')
    p = get_cfg(f'{args.claim}.n_splits')
    a = get_cfg(f'{args.claim}.n_alpha')
    if a is None: a = get_cfg(f'{args.claim}.alpha') # Check alt key

    # Load Data
    X_train, y_train = dataio.load_split(data_dir, 'train')
    X_test, y_test = dataio.load_split(data_dir, 'test')
    
    n_train = X_train.shape[0]
    n_test = X_test.shape[0]
    
    # Balancing for Monks-2 (handled in dataio via oversampling inside loop or globally?)
    # Paper: "For the MONK's problem ... we use ... the adaptive random partition."
    # Let's apply oversampling to the training set before fitting the forest for Monks.
    if dataset_name == 'monks':
        ros = RandomOverSampler(random_state=args.seed)
        X_train, y_train = ros.fit_resample(X_train, y_train)

    start_time = time.time()
    
    # Train Forest
    forest = []
    for i in range(m):
        # Bootstrap sample
        indices = np.random.choice(len(X_train), size=len(X_train), replace=True)
        X_boot = X_train[indices]
        y_boot = y_train[indices]
        
        tree = BestScoredRandomTree(n_candidates=k, n_splits=p, alpha=a)
        # Fix internal random state for reproducibility of the candidate generation logic if needed,
        # but np.random global seed is set below.
        tree.fit(X_boot, y_boot)
        forest.append(tree)
    
    # Prediction
    preds_list = []
    for tree in forest:
        preds_list.append(tree.predict(X_test))
    
    preds_array = np.array(preds_list) # Shape (m, n_test)
    
    # Majority Vote
    # For binary 0/1, sum > m/2 means 1.
    votes = np.sum(preds_array, axis=0)
    final_preds = (votes > m / 2).astype(int)
    
    end_time = time.time()
    train_seconds = end_time - start_time
    
    # Metric: Classification Error
    error = 1.0 - np.mean(final_preds == y_test)
    
    output = {
        "claim": args.claim,
        "seed": args.seed,
        "metric": "classification_error",
        "value": error,
        "train_seconds": train_seconds,
        "n_train": n_train, # Original size before balancing
        "n_test": n_test,
        "config_overrides": args.set
    }
    
    print(json.dumps(output))

if __name__ == '__main__':
    np.random.seed(int(sys.argv[sys.argv.index('--seed')+1]))
    main()

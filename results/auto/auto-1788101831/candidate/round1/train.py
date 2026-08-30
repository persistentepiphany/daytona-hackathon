import argparse
import json
import time
import numpy as np
import os
import sys
from sklearn.model_selection import KFold

# Import dataio from current directory
import dataio

def parse_config_override(overrides):
    cfg = {}
    for o in overrides:
        if '=' in o:
            k, v = o.split('=', 1)
            keys = k.split('.')
            d = cfg
            for key_i in keys[:-1]:
                if key_i not in d: d[key_i] = {}
                d = d[key_i]
            try:
                d[keys[-1]] = int(v)
            except ValueError:
                try:
                    d[keys[-1]] = float(v)
                except ValueError:
                    d[keys[-1]] = v
    return cfg

def adaptive_random_partition(X, y, p_splits, a_param):
    """
    Constructs a partition structure (list of nodes) for a tree.
    Returns a function/class that can predict.
    Adaptive: Pick a random sample point, then pick the node containing it to split.
    """
    n, d = X.shape
    # Node representation: [feature_idx, threshold, left_child_idx, right_child_idx, is_leaf, class_label]
    # We'll store nodes in a list. 0 is root.
    nodes = []
    # Initialize root with all data indices
    # For efficiency with numpy, we store a mask or indices in a list parallel to nodes? 
    # Since n is small (<1000 for these datasets), we can store indices.
    
    # Tree structure: list of dictionaries
    tree = [{'indices': np.arange(n), 'left': None, 'right': None, 'feat': None, 'thresh': None, 'label': None}]
    
    # Pre-calculate min/max for features to handle valid splits
    feat_mins = X.min(axis=0)
    feat_maxs = X.max(axis=0)
    
    for _ in range(p_splits):
        # Identify leaf nodes
        leaf_indices = [i for i, node in enumerate(tree) if node['left'] is None]
        if not leaf_indices: break
        
        # Adaptive selection: Pick random sample point, find its leaf
        rand_samp_idx = np.random.randint(0, n)
        target_node_idx = -1
        
        # Traverse to find where rand_samp_idx falls
        curr = 0
        while tree[curr]['left'] is not None:
            feat = tree[curr]['feat']
            thresh = tree[curr]['thresh']
            if X[rand_samp_idx, feat] <= thresh:
                curr = tree[curr]['left']
            else:
                curr = tree[curr]['right']
        target_node_idx = curr
        
        # If this leaf has < 2 samples (or is pure), stop splitting it? 
        # Paper doesn't explicitly say, but standard tree practice.
        # We'll just try to split. If split is invalid, do nothing.
        node = tree[target_node_idx]
        idxs = node['indices']
        if len(idxs) <= 1:
            continue
            
        # Choose random feature dimension
        feat = np.random.randint(0, d)
        
        # Check if feature has variance
        col_vals = X[idxs, feat]
        if np.min(col_vals) == np.max(col_vals):
            continue
            
        # Choose cut point in [0.5-a, 0.5+a] normalized range
        # Paper: "parameter a in [0,0.5] in the uniform distribution Unif[0.5-a, 0.5+a]"
        # This implies the cut point is a percentile of the data range? Or just a fraction of (min, max)?
        # Interpretation: Splits are random. Standard random forest: uniform in [min, max].
        # Here they define a distribution around the midpoint.
        # We implement: split = min + U * (max - min), where U ~ Unif[0.5-a, 0.5+a].
        # We need to clip U to [0, 1]. If a=0.5, range is [0, 1].
        
        u = np.random.uniform(0.5 - a_param, 0.5 + a_param)
        u = np.clip(u, 0.0, 1.0)
        
        min_val = feat_mins[feat] # Should probably use node-specific min/max for robustness
        max_val = feat_maxs[feat]
        
        # Node specific min/max
        n_min = np.min(col_vals)
        n_max = np.max(col_vals)
        
        threshold = n_min + u * (n_max - n_min)
        
        # Split indices
        left_mask = X[idxs, feat] <= threshold
        left_idxs = idxs[left_mask]
        right_idxs = idxs[~left_mask]
        
        if len(left_idxs) == 0 or len(right_idxs) == 0:
            continue # Invalid split, try again next step (we count this as a step used? Paper implies p is max splits)
        
        # Update tree
        tree[target_node_idx]['feat'] = feat
        tree[target_node_idx]['thresh'] = threshold
        
        tree[target_node_idx]['left'] = len(tree)
        tree.append({'indices': left_idxs, 'left': None, 'right': None, 'feat': None, 'thresh': None, 'label': None})
        
        tree[target_node_idx]['right'] = len(tree)
        tree.append({'indices': right_idxs, 'left': None, 'right': None, 'feat': None, 'thresh': None, 'label': None})
        
    # Labeling (Majority Vote)
    for node in tree:
        if node['left'] is None: # Leaf
            vals = y[node['indices']]
            # Classes are 0 and 1
            c0 = np.sum(vals == 0)
            c1 = np.sum(vals == 1)
            if c0 >= c1:
                node['label'] = 0
            else:
                node['label'] = 1
                
    return tree

def predict_tree(tree, x):
    curr = 0
    while tree[curr]['left'] is not None:
        feat = tree[curr]['feat']
        thresh = tree[curr]['thresh']
        if x[feat] <= thresh:
            curr = tree[curr]['left']
        else:
            curr = tree[curr]['right']
    return tree[curr]['label']

def cv_score(X, y, k_candidates, p_splits, a_param):
    """
    Generate k candidates partitions, select best via 10-fold CV.
    Returns the best tree model trained on full X,y.
    Paper: "generate k p-splitting adaptive random partitions... choose partition with best... via 10-fold CV"
    """
    kf = KFold(n_splits=10, shuffle=True, random_state=42) # Seed for reproducibility of CV
    
    best_score = -np.inf
    best_tree_structure = None # Actually we need to regenerate the winning structure on full data
    best_config = None # (feat, thresh pairs) or just the random seed?
    
    # To replicate the "best partition", we need to know what random choices led to it.
    # The paper says: "choose the partition... to be the exact partition for one tree. Furthermore, by giving labels to all the cells... basing on the training data (70%)"
    # Implementation: We cannot easily serialize the random choices without saving them.
    # Approximation: We will identify the winning strategy by the random seed used to generate that candidate.
    # Then we regenerate it.
    
    # Generate k candidates
    # For reproducibility within the run, we use a stream of seeds.
    # We store the validation error for each seed.
    
    errors = []
    
    # We need a source of randomness for candidates
    # We'll use a loop. For reproducibility, we assume we can just generate k trees on the 70% data (which is X here)
    # and validate.
    
    # Note: The "10-fold cross-validation" is performed on the training set (X, y).
    # We evaluate k candidates.
    
    # To support reproducibility of the "best" one, we can just re-run the generator for the best one.
    
    best_seed = None
    
    for i in range(k_candidates):
        # Generate a seed for this candidate
        cand_seed = np.random.randint(0, 2**32)
        
        avg_val_error = 0.0
        for train_idx, val_idx in kf.split(X):
            X_tr, X_val = X[train_idx], X[val_idx]
            y_tr, y_val = y[train_idx], y[val_idx]
            
            # Set seed for this candidate generation
            rng_state = np.random.get_state()
            np.random.seed(cand_seed)
            tree = adaptive_random_partition(X_tr, y_tr, p_splits, a_param)
            np.random.set_state(rng_state)
            
            # Predict on validation
            preds = np.array([predict_tree(tree, xv) for xv in X_val])
            error = np.mean(preds != y_val)
            avg_val_error += error
            
        avg_val_error /= 10.0
        
        if avg_val_error < best_score or best_seed is None:
            best_score = avg_val_error
            best_seed = cand_seed
            
    # Retrain best tree on full training data
    np.random.seed(best_seed)
    final_tree = adaptive_random_partition(X, y, p_splits, a_param)
    
    return final_tree

def build_forest(X_train, y_train, n_trees, k_candidates, p_splits, a_param):
    forest = []
    for _ in range(n_trees):
        # For the paper method, trees are independent. 
        # The "best-scored" logic applies per tree.
        # The randomness for the "k candidates" is independent per tree? 
        # "generate k p-splitting... choose the best... we obtain all m trees"
        # This implies the whole process repeats m times.
        tree = cv_score(X_train, y_train, k_candidates, p_splits, a_param)
        forest.append(tree)
    return forest

def predict_forest(forest, x):
    preds = [predict_tree(t, x) for t in forest]
    # Majority vote
    vals, counts = np.unique(preds, return_counts=True)
    return vals[np.argmax(counts)]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claim', type=str, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--set', action='append', default=[])
    args = parser.parse_args()
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Apply overrides
    overrides = parse_config_override(args.set)
    
    # Merge overrides into config (simple recursive merge needed? We just override specific keys)
    # Handling nested overrides properly for the claim config
    # Note: overrides like 'n_trees=10' should apply to the claim params.
    
    claim_config = config[args.claim].copy()
    
    # Helper to update dict
    def update_dict(d, u):
        for k, v in u.items():
            if isinstance(v, dict):
                d[k] = update_dict(d.get(k, {}), v)
            else:
                d[k] = v
        return d
    
    if overrides:
        claim_config = update_dict(claim_config, overrides)
        if 'data' in overrides:
            config['data'] = update_dict(config.get('data', {}), overrides['data'])
    
    # Set seeds
    np.random.seed(args.seed)
    
    # Load Data
    data_dir = config['data']['dir']
    dataset_name = claim_config['dataset']
    
    if dataset_name == 'monks':
        (X_train, y_train), (X_test, y_test) = dataio.load_monks(data_dir)
    elif dataset_name == 'bcw':
        (X_train, y_train), (X_test, y_test) = dataio.load_bcw(data_dir)
    else:
        raise ValueError(f"Unknown dataset {dataset_name}")
    
    # Params
    n_trees = claim_config.get('n_trees', 10) 
    k_cand = claim_config.get('k_candidates', 5)
    p_spl = claim_config.get('p_splits', 10)
    a_par = claim_config.get('a_param', 0.1)
    n_reps = claim_config.get('n_repetitions', 1) # The runner script controls repetitions via --seed usually, but we support it.
    
    # Train
    start_time = time.time()
    forest = build_forest(X_train, y_train, n_trees, k_cand, p_spl, a_par)
    train_time = time.time() - start_time
    
    # Test
    preds = np.array([predict_forest(forest, x) for x in X_test])
    error = np.mean(preds != y_test)
    
    result = {
        "claim": args.claim,
        "seed": args.seed,
        "metric": "classification_error",
        "value": float(error),
        "train_seconds": float(train_time),
        "n_train": int(X_train.shape[0]),
        "n_test": int(X_test.shape[0]),
        "config_overrides": args.set
    }
    
    print(json.dumps(result))

if __name__ == '__main__':
    main()

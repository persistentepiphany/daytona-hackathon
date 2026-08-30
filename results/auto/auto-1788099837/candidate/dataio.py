import os
import numpy as np
import gzip


def _read_images(path):
    with gzip.open(path, 'rb') as f:
        data = np.frombuffer(f.read(), np.uint8, offset=16)
    return data.reshape(-1, 28 * 28).astype(np.float32) / 255.0


def _read_labels(path):
    with gzip.open(path, 'rb') as f:
        data = np.frombuffer(f.read(), np.uint8, offset=8)
    return data.astype(np.int64)


def load_split(data_dir, split):
    if split == "train":
        X = _read_images(os.path.join(data_dir, "train-images-idx3-ubyte.gz"))
        y = _read_labels(os.path.join(data_dir, "train-labels-idx1-ubyte.gz"))
    elif split == "test":
        X = _read_images(os.path.join(data_dir, "t10k-images-idx3-ubyte.gz"))
        y = _read_labels(os.path.join(data_dir, "t10k-labels-idx1-ubyte.gz"))
    else:
        raise ValueError(f"Unknown split: {split}")
    return X, y
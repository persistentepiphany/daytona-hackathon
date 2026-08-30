# Reproduction report: Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms

*failure to reproduce is evidence the paper as written is insufficient to reconstruct the result - not evidence the authors are wrong*

## Run lineage

1. Run id: `auto-1788099837`
2. Preregistration hash: `adc904610e833b6c18c5c0bfb4438a33d730695ce4bfacc11efb93a0faf79462`
3. Frozen snapshot S0: `s0-auto-1788099837` (recipe `6fd074d34471dfb2600f17220e74172462ce335a03e35e247f421b5a55c5b0ed`, git `ade625b1ab11e68ac8fb86e0fc774211d9acc9be`)
4. Paper hash: `253b6ef70144cb56d13b4e67d3f8cafad42df78d5984265e0a6e4812ad1715ae`

## Controls (scored before the target rows)

1. Calibration: this run is itself the calibration paper run.
2. Hermeticity: NOT RUN - autonomous smoke path

## Sham twin (corrupted targets; expected NOT REPRODUCED)

| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict | Rule | Attempts | Evidence |
|---|---|---|---|---|---|---|---|---|---|

## Primary preregistered results

| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict | Rule | Attempts | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| exp_dt | dt_fashion_1 | reproduce | no | 0.81102 | -0.06198 | **REPRODUCED OUTSIDE PREREGISTERED TOLERANCE** | rule_dt | att-7c97ea7a | cfa40dcef387 |
| exp_svc | svc_fashion_1 | reproduce | no | None | None | **NOT ATTEMPTABLE** | rule_svc |  |  |

## Code-absence certification

1. Status: None
2. Queries: 
3. GitHub - zalandoresearch/fashion-mnist: A MNIST-like fashion product database. Benchmark · GitHub - https://github.com/zalandoresearch/fashion-mnist
4. Fashion MNIST - https://www.kaggle.com/datasets/zalando-research/fashionmnist
5. [1708.07747] Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms - https://arxiv.org/abs/1708.07747
6. Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms - ADS - http://ui.adsabs.harvard.edu/abs/2017arXiv170807747X/abstract
7. fashion_mnist - Datasets - https://www.tensorflow.org/datasets/catalog/fashion_mnist
8. Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms - https://arxiv.org/pdf/1708.07747
9. github.com-zalandoresearch-fashion-mnist_-_2017-08-26_10-19-41 : zalandoresearch : Free Download, Borrow, and Streaming : Internet Archive - https://archive.org/details/github.com-zalandoresearch-fashion-mnist_-_2017-08-26_10-19-41
10. fashion-mnist/README.md at master · zalandoresearch/fashion-mnist · GitHub - https://github.com/zalandoresearch/fashion-mnist/blob/master/README.md


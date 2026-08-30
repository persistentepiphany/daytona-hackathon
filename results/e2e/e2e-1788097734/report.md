# Reproduction report: Fashion-MNIST (arXiv:1708.07747)

*failure to reproduce is evidence the paper as written is insufficient to reconstruct the result - not evidence the authors are wrong*

## Run lineage

1. Run id: `e2e-1788097734`
2. Preregistration hash: `e5251f19be29fe7b67716ea1b82e5cca71c3a14788ad838d40e95bb2bdcb534c`
3. Frozen snapshot S0: `s0-e2e-1788097734` (recipe `e7b334101d3dae3a45fa6a2b78a2e4234e44e9edc8c678d8e9fe0e6c1e437b38`, git `f1e6399a5b744519637f8be35e3ff37e94965f5c`)
4. Paper hash: `253b6ef70144cb56d13b4e67d3f8cafad42df78d5984265e0a6e4812ad1715ae`

## Controls (scored before the target rows)

1. Calibration: this run is itself the calibration paper run.
2. Hermeticity: VERIFIED - network_block_all active, run completed (mean=0.5856)

## Sham twin (corrupted targets; expected NOT REPRODUCED)

| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict | Rule | Attempts | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| SH01 | C1 | reproduce | no | 0.811233 | -0.036767 | **NOT REPRODUCED** | R-SH01 | att-3846997c | 9ce1c715e996 |

## Primary preregistered results

| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict | Rule | Attempts | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| E001 | C1 | reproduce | no | 0.811233 | 0.013233 | **REPRODUCED WITHIN TOLERANCE** | R-E001 | att-17cae7e8 | cdf68ca06f30 |
| E004 | C4 | reproduce | no | 0.5856 | 0.0746 | **NOT REPRODUCED** | R-E004 | att-89b4b3de | b699f4bdc44c |

## Code-absence certification

1. Status: FOUND
2. Queries: 8 issued
3. GitHub - zalandoresearch/fashion-mnist: A MNIST-like fashion product database. Benchmark · GitHub - https://github.com/zalandoresearch/fashion-mnist
4. Fashion MNIST - https://www.kaggle.com/datasets/zalando-research/fashionmnist
5. [1708.07747] Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms - https://arxiv.org/abs/1708.07747
6. Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms - ADS - http://ui.adsabs.harvard.edu/abs/2017arXiv170807747X/abstract
7. fashion_mnist - Datasets - https://www.tensorflow.org/datasets/catalog/fashion_mnist
8. Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms - https://arxiv.org/pdf/1708.07747
9. github.com-zalandoresearch-fashion-mnist_-_2017-08-26_10-19-41 : zalandoresearch : Free Download, Borrow, and Streaming : Internet Archive - https://archive.org/details/github.com-zalandoresearch-fashion-mnist_-_2017-08-26_10-19-41
10. fashion-mnist/README.md at master · zalandoresearch/fashion-mnist · GitHub - https://github.com/zalandoresearch/fashion-mnist/blob/master/README.md


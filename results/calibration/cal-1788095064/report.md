# Reproduction report: Fashion-MNIST (arXiv:1708.07747)

*failure to reproduce is evidence the paper as written is insufficient to reconstruct the result - not evidence the authors are wrong*

## Run lineage

1. Run id: `cal-1788095064`
2. Preregistration hash: `8463f589613c06cfad58deb17cdd88c5cd0a7cbaf29e6f315c0f4a1e65d7f6f6`
3. Frozen snapshot S0: `s0-fashion-mnist-cal-1788095064` (recipe `e7b334101d3dae3a45fa6a2b78a2e4234e44e9edc8c678d8e9fe0e6c1e437b38`, git `61de8c715b5e071fb3ebc156facd911c6f523c0a`)
4. Paper hash: `253b6ef70144cb56d13b4e67d3f8cafad42df78d5984265e0a6e4812ad1715ae`

## Controls (scored before the target rows)

1. Calibration: this run is itself the calibration paper run.
2. Hermeticity: VERIFIED - network_block_all active, run completed (mean=0.5856)

## Sham twin (corrupted targets; expected NOT REPRODUCED)

| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict | Rule | Attempts | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| SH01 | C4 | reproduce | no | 0.5856 | 0.0246 | **REPRODUCED OUTSIDE PREREGISTERED TOLERANCE** | R-SH01 | att-f53a3e40 | 215a5260139f |
| SH02 | C1 | reproduce | no | 0.81112 | -0.03688 | **NOT REPRODUCED** | R-SH02 | att-e2cccc77 | dfbcacb463f4 |

## Primary preregistered results

| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict | Rule | Attempts | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| E001 | C1 | reproduce | no | 0.81112 | 0.01312 | **REPRODUCED WITHIN TOLERANCE** | R-E001 | att-14300f94, att-f39aca1b | 6fc946cecb59 |
| E002 | C2 | reproduce | no | 0.87776 | 0.00476 | **REPRODUCED WITHIN TOLERANCE** | R-E002 | att-36686991, att-5c32261d | 03db68402241 |
| E003 | C3 | reproduce | no | None | None | **NOT ATTEMPTABLE** | R-E003 | att-c018384f, att-b4d95194 |  |
| E004 | C4 | reproduce | no | 0.5856 | 0.0746 | **NOT REPRODUCED** | R-E004 | att-d10f3ca7, att-244cd3f5 | 3399ee9bced8 |
| E006 | C7 | reproduce | no | 0.80084 | 0.01184 | **REPRODUCED WITHIN TOLERANCE** | R-E006 | att-c43c8f55 | d836d83a2eef |
| E101 | C2 | ablation | no | 0.85652 | -0.02124 | **CONTROL PASS** | R-E101 | att-567132a4 | 8135a65f56eb |
| E102 | C1 | randomized_control | no | 0.11336 | 0.01336 | **CONTROL PASS** | R-E102 | att-3bc16a7f | 4991bffe8846 |
| E005 | C5 | reproduce | yes | 0.76996 | -0.01204 | **REPRODUCED WITHIN TOLERANCE** | R-E005 | att-49d59618 | 06e656fbd0d4 |

## ADAPTIVE round (cannot alter primary verdicts)

| Experiment | Claim | Type | Held-out | Observed | Delta | Verdict | Rule | Attempts | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| A201 | C4 | ablation | no | 0.5856 | 0.0746 | **NOT REPRODUCED** | R-A201 | att-438e89df | 38a07260e1d2 |

## Code-absence certification

1. Status: COMPLETED
2. Queries: Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms official source code repository, Fashion-MNIST zalandoresearch github official
3. GitHub - zalandoresearch/fashion-mnist: A MNIST-like fashion product database. Benchmark · GitHub - https://github.com/zalandoresearch/fashion-mnist
4. Fashion MNIST - https://www.kaggle.com/datasets/zalando-research/fashionmnist
5. [1708.07747] Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms - https://arxiv.org/abs/1708.07747
6. Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms - ADS - http://ui.adsabs.harvard.edu/abs/2017arXiv170807747X/abstract
7. fashion_mnist - Datasets - https://www.tensorflow.org/datasets/catalog/fashion_mnist
8. Fashion-MNIST: a Novel Image Dataset for Benchmarking Machine Learning Algorithms - https://arxiv.org/pdf/1708.07747
9. github.com-zalandoresearch-fashion-mnist_-_2017-08-26_10-19-41 : zalandoresearch : Free Download, Borrow, and Streaming : Internet Archive - https://archive.org/details/github.com-zalandoresearch-fashion-mnist_-_2017-08-26_10-19-41
10. fashion-mnist/README.md at master · zalandoresearch/fashion-mnist · GitHub - https://github.com/zalandoresearch/fashion-mnist/blob/master/README.md


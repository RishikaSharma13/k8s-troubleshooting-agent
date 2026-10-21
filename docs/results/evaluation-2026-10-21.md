# Memory forecast evaluation: namespace `predict-lab`

Generated 2026-10-21 05:56 UTC. Parameters: origin_step_s=60.0, horizon_min=30.0, lookback_min=15.0, since=None, until=2026-10-21T05:51

## Data

| Deployment | Lives | OOMKilled (scored) | Negative (scored) | Excluded | Samples | From | To |
|---|---|---|---|---|---|---|---|
| flat-hot | 3 | 0 | 3 | 0 | 1672 | 10-19 07:58 | 10-21 05:50 |
| leak-slow | 15 | 10 | 0 | 5 | 1908 | 10-19 07:58 | 10-21 05:50 |
| leak-steady | 27 | 22 | 0 | 5 | 1898 | 10-19 07:58 | 10-21 05:50 |

Excluded lives: OOMKilled but starts after a collector gap (incomplete): 4, ends at a collector gap: 3, still running: 2, too short (<8 samples): 1.

## flat-hot

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 2.60 |
| persistence | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 1.53 |
| prophet | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 2.66 |
| threshold80 | 0 | 0 | - | - | 0 | 53% | 76 | - | - | - | - | - | - | - |
| threshold90 | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | - |

Aliasing check: threshold alert share over every sample (not just decision points): threshold80: 49%, threshold90: 0%.

## leak-slow

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 10 | 0 | 29.4 | 29.0 | 1 | - | 0 | +0.06 | 0.06 | 50% | +0.07 | 0.07 | 40% | 0.24 |
| persistence | 10 | 10 | - | - | 0 | - | 0 | - | - | - | - | - | - | 30.19 |
| prophet | 10 | 0 | 29.1 | 28.8 | 0 | - | 0 | +0.04 | 0.06 | 60% | +0.06 | 0.06 | 50% | 0.28 |
| threshold80 | 10 | 0 | 7.7 | 7.5 | 0 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 10 | 0 | 3.3 | 3.1 | 0 | - | 0 | - | - | - | - | - | - | - |

## leak-steady

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 22 | 0 | 18.5 | 17.3 | 0 | - | 0 | +0.02 | 0.03 | 82% | +0.02 | 0.02 | 95% | 0.56 |
| persistence | 22 | 22 | - | - | 0 | - | 0 | - | - | - | - | - | - | 60.21 |
| prophet | 22 | 0 | 18.5 | 17.3 | 0 | - | 0 | +0.03 | 0.05 | 68% | +0.04 | 0.04 | 68% | 0.58 |
| threshold80 | 22 | 0 | 3.6 | 2.9 | 0 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 22 | 0 | 1.4 | 1.1 | 0 | - | 0 | - | - | - | - | - | - | - |

## How to read this

- Bias > 0 means the forecast was late: it promised more time than there was.
- Coverage is how often the actual time-to-OOMKill fell inside the forecast's interval.
- '-' means not applicable or no data (thresholds and persistence give no time-to-limit).
- Results come from synthetic, controlled workloads in one kind cluster; they demonstrate the method, they are not general accuracy rates.

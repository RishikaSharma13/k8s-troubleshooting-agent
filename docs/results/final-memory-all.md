# Memory forecast evaluation: namespace `predict-lab`

Generated 2026-10-26 05:42 UTC. Parameters: origin_step_s=60.0, horizon_min=30.0, lookback_min=15.0, since=None, until=2026-10-26T04:27

## Data

| Deployment | Lives | OOMKilled (scored) | Negative (scored) | Excluded | Samples | From | To |
|---|---|---|---|---|---|---|---|
| disk-flat | 2 | 0 | 2 | 0 | 14400 | 10-22 06:42 | 10-26 04:26 |
| disk-leak | 183 | 0 | 17 | 166 | 16784 | 10-22 06:42 | 10-26 04:26 |
| flat-hot | 4 | 0 | 4 | 0 | 21278 | 10-19 07:58 | 10-26 04:26 |
| leak-slow | 164 | 156 | 0 | 8 | 24471 | 10-19 07:58 | 10-26 04:26 |
| leak-steady | 326 | 315 | 0 | 11 | 24298 | 10-19 07:58 | 10-26 04:26 |

Excluded lives: Error: 165, OOMKilled but starts after a collector gap (incomplete): 6, ends at a collector gap: 4, still running: 2, too short (<8 samples): 5, unrecorded_restart: 3.

## disk-flat

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 0.00 |
| persistence | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 0.00 |
| prophet | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 0.00 |
| threshold80 | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | - |
| threshold90 | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | - |

Aliasing check: threshold alert share over every sample (not just decision points): threshold80: 0%, threshold90: 0%.

## disk-leak

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 0.29 |
| persistence | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 0.37 |
| prophet | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 0.16 |
| threshold80 | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | - |
| threshold90 | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | - |

Aliasing check: threshold alert share over every sample (not just decision points): threshold80: 0%, threshold90: 0%.

## flat-hot

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 2.56 |
| persistence | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 1.78 |
| prophet | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | 2.60 |
| threshold80 | 0 | 0 | - | - | 0 | 51% | 964 | - | - | - | - | - | - | - |
| threshold90 | 0 | 0 | - | - | 0 | 0% | 0 | - | - | - | - | - | - | - |

Aliasing check: threshold alert share over every sample (not just decision points): threshold80: 50%, threshold90: 0%.

## leak-slow

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 156 | 0 | 29.2 | 28.7 | 2 | - | 0 | +0.05 | 0.05 | 63% | +0.06 | 0.06 | 54% | 0.25 |
| persistence | 156 | 156 | - | - | 0 | - | 0 | - | - | - | - | - | - | 30.16 |
| prophet | 156 | 0 | 29.3 | 28.7 | 6 | - | 0 | +0.06 | 0.06 | 51% | +0.07 | 0.07 | 43% | 0.29 |
| threshold80 | 156 | 0 | 7.9 | 7.2 | 0 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 156 | 0 | 3.6 | 2.9 | 0 | - | 0 | - | - | - | - | - | - | - |

## leak-steady

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 315 | 0 | 18.5 | 17.2 | 0 | - | 0 | +0.03 | 0.04 | 77% | +0.03 | 0.03 | 94% | 0.61 |
| persistence | 315 | 315 | - | - | 0 | - | 0 | - | - | - | - | - | - | 60.20 |
| prophet | 315 | 0 | 18.5 | 17.2 | 0 | - | 0 | +0.03 | 0.04 | 67% | +0.03 | 0.03 | 80% | 0.64 |
| threshold80 | 315 | 0 | 3.6 | 2.8 | 0 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 315 | 0 | 1.6 | 1.0 | 0 | - | 0 | - | - | - | - | - | - | - |

## How to read this

- Bias > 0 means the forecast was late: it promised more time than there was.
- Coverage is how often the actual time-to-OOMKill fell inside the forecast's interval.
- '-' means not applicable or no data (thresholds and persistence give no time-to-limit).
- Results come from synthetic, controlled workloads in one kind cluster; they demonstrate the method, they are not general accuracy rates.

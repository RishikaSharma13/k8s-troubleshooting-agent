# Memory forecast evaluation: namespace `pattern-lab`

Generated 2026-11-20 10:39 UTC. Parameters: origin_step_s=60.0, horizon_min=30.0, lookback_min=45.0, since=2026-11-20T04:00, until=2026-11-20T10:30, failing lives excluded because they started before --since=3

## Data

| Deployment | Lives | OOMKilled (scored) | Negative (scored) | Excluded | Samples | From | To |
|---|---|---|---|---|---|---|---|
| cycle-leak | 4 | 3 | 0 | 1 | 1278 | 11-20 04:42 | 11-20 10:29 |
| noisy-leak | 6 | 5 | 0 | 1 | 1419 | 11-20 04:00 | 11-20 10:29 |
| step-leak | 8 | 7 | 0 | 1 | 1273 | 11-20 04:37 | 11-20 10:29 |

Excluded lives: still running: 2, too short (<8 samples): 1.

## cycle-leak

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Sustained warning median (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 3 | 0 | 98.1 | 98.1 | 34.6 | 28 | - | 0 | -0.99 | 0.99 | 100% | +2.78 | 2.78 | 100% | 31.61 |
| persistence | 3 | 3 | - | - | 0.0 | 0 | - | 0 | - | - | - | - | - | - | 26.94 |
| prophet | 3 | 0 | 98.1 | 98.1 | 35.3 | 31 | - | 0 | +0.62 | 0.93 | 100% | +2.88 | 2.88 | 100% | 30.92 |
| threshold80 | 3 | 0 | 35.3 | 34.7 | 19.4 | 4 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 3 | 0 | 17.7 | 17.5 | 1.4 | 0 | - | 0 | - | - | - | - | - | - | - |

## noisy-leak

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Sustained warning median (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 5 | 0 | 55.4 | 21.9 | 21.9 | 11 | - | 0 | +9.05 | 9.05 | 40% | +8.90 | 8.90 | 40% | 7.77 |
| persistence | 5 | 5 | - | - | 0.0 | 0 | - | 0 | - | - | - | - | - | - | 17.00 |
| prophet | 5 | 0 | 55.4 | 23.0 | 23.0 | 10 | - | 0 | +8.79 | 8.79 | 40% | +8.67 | 8.67 | 40% | 7.79 |
| threshold80 | 5 | 0 | 11.4 | 3.6 | 3.8 | 0 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 5 | 2 | 11.4 | 5.7 | 0.0 | 0 | - | 0 | - | - | - | - | - | - | - |

## step-leak

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Sustained warning median (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 7 | 0 | 32.0 | 30.9 | 32.0 | 16 | - | 0 | -1.16 | 1.16 | 100% | -1.28 | 1.28 | 100% | 7.64 |
| persistence | 7 | 7 | - | - | 0.0 | 0 | - | 0 | - | - | - | - | - | - | 25.16 |
| prophet | 7 | 0 | 32.0 | 30.9 | 32.0 | 16 | - | 0 | -1.13 | 1.13 | 100% | -1.24 | 1.24 | 100% | 7.64 |
| threshold80 | 7 | 0 | 9.4 | 9.0 | 9.4 | 0 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 7 | 0 | 4.6 | 4.0 | 4.6 | 0 | - | 0 | - | - | - | - | - | - | - |

## How to read this

- Bias > 0 means the forecast was late: it promised more time than there was.
- Lead time counts from the first alert; sustained warning counts only the alert run still on at the end, so alerts that flicker on and off early cannot inflate it.
- Coverage is how often the actual time-to-OOMKill fell inside the forecast's interval.
- '-' means not applicable or no data (thresholds and persistence give no time-to-limit).
- Results come from synthetic, controlled workloads in one kind cluster; they demonstrate the method, they are not general accuracy rates.

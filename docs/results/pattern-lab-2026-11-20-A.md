# Memory forecast evaluation: namespace `pattern-lab`

Generated 2026-11-20 10:36 UTC. Parameters: origin_step_s=60.0, horizon_min=30.0, lookback_min=15.0, since=2026-11-19T05:30, until=2026-11-20T10:30, failing lives excluded because they started before --since=0

## Data

| Deployment | Lives | OOMKilled (scored) | Negative (scored) | Excluded | Samples | From | To |
|---|---|---|---|---|---|---|---|
| cycle-leak | 10 | 7 | 0 | 3 | 3091 | 11-19 05:30 | 11-20 10:29 |
| noisy-leak | 14 | 11 | 0 | 3 | 3087 | 11-19 05:30 | 11-20 10:29 |
| step-leak | 19 | 16 | 0 | 3 | 3080 | 11-19 05:30 | 11-20 10:29 |

Excluded lives: OOMKilled but starts after a collector gap (incomplete): 3, ends at a collector gap: 2, still running: 2, too short (<8 samples): 2.

## cycle-leak

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Sustained warning median (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 7 | 0 | 98.2 | 98.1 | 0.0 | 186 | - | 0 | -8.84 | 8.84 | 0% | - | - | - | 55.47 |
| persistence | 7 | 7 | - | - | 0.0 | 0 | - | 0 | - | - | - | - | - | - | 27.01 |
| prophet | 7 | 0 | 98.2 | 98.1 | 0.0 | 183 | - | 0 | -8.81 | 8.81 | 0% | - | - | - | 55.47 |
| threshold80 | 7 | 0 | 35.2 | 34.7 | 5.6 | 9 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 7 | 0 | 17.8 | 17.5 | 1.6 | 0 | - | 0 | - | - | - | - | - | - | - |

## noisy-leak

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Sustained warning median (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 11 | 0 | 55.4 | 17.1 | 25.6 | 36 | - | 0 | +5.62 | 5.62 | 36% | +9.77 | 9.77 | 36% | 9.36 |
| persistence | 11 | 11 | - | - | 0.0 | 0 | - | 0 | - | - | - | - | - | - | 18.11 |
| prophet | 11 | 0 | 55.4 | 17.1 | 25.6 | 37 | - | 0 | +5.66 | 5.66 | 27% | +9.80 | 9.80 | 36% | 9.37 |
| threshold80 | 11 | 0 | 15.9 | 3.6 | 10.2 | 0 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 11 | 4 | 15.9 | 5.3 | 1.6 | 0 | - | 0 | - | - | - | - | - | - | - |

## step-leak

| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Sustained warning median (min) | Early alerts | False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 16 | 0 | 33.2 | 32.6 | 32.9 | 51 | - | 0 | -0.06 | 0.24 | 100% | -0.62 | 0.62 | 100% | 7.79 |
| persistence | 16 | 16 | - | - | 0.0 | 0 | - | 0 | - | - | - | - | - | - | 25.24 |
| prophet | 16 | 0 | 33.2 | 32.6 | 32.9 | 51 | - | 0 | -0.04 | 0.27 | 100% | -0.66 | 0.66 | 100% | 7.80 |
| threshold80 | 16 | 0 | 9.5 | 8.9 | 9.5 | 0 | - | 0 | - | - | - | - | - | - | - |
| threshold90 | 16 | 0 | 4.5 | 3.9 | 4.5 | 0 | - | 0 | - | - | - | - | - | - | - |

## How to read this

- Bias > 0 means the forecast was late: it promised more time than there was.
- Lead time counts from the first alert; sustained warning counts only the alert run still on at the end, so alerts that flicker on and off early cannot inflate it.
- Coverage is how often the actual time-to-OOMKill fell inside the forecast's interval.
- '-' means not applicable or no data (thresholds and persistence give no time-to-limit).
- Results come from synthetic, controlled workloads in one kind cluster; they demonstrate the method, they are not general accuracy rates.

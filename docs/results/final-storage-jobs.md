# Storage and Job-deadline evaluation: namespace `predict-lab`

Generated 2026-10-26 04:57 UTC; data from 2026-10-22T07:25 until 2026-10-26T04:27 (UTC). Each decision used only readings available at that moment.

Excluded because they started before the evaluation window: storage lives: 1, jobs: 7.

## Storage: predicting eviction (ephemeral-storage limit)

| Deployment | Lives | Evicted (scored) | Negative (scored) | Excluded |
|---|---|---|---|---|
| disk-flat | 2 | 0 | 2 | 0 |
| disk-leak | 181 | 178 | 0 | 3 |

Excluded: Evicted but starts after a collector gap (incomplete): 1, too short (<5 samples): 2.

### disk-flat

| Method | Evicted lives | Missed | Warning median (min) | Warning min (min) | False-alarm rate | False-alarm episodes | No forecast (too little data) | Bias @10m (min) | Coverage @10m | Bias @5m | Coverage @5m |
|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 0 | 0 | - | - | 0% | 0 | 1% | - | - | - | - |
| prophet | 0 | 0 | - | - | 0% | 0 | 1% | - | - | - | - |
| threshold80 | 0 | 0 | - | - | 100% | 2 | - | - | - | - | - |
| threshold90 | 0 | 0 | - | - | 0% | 0 | - | - | - | - | - |

Threshold alert share over every sample: threshold80: 100%, threshold90: 0%.

### disk-leak

| Method | Evicted lives | Missed | Warning median (min) | Warning min (min) | False-alarm rate | False-alarm episodes | No forecast (too little data) | Bias @10m (min) | Coverage @10m | Bias @5m | Coverage @5m |
|---|---|---|---|---|---|---|---|---|---|---|---|
| linear | 178 | 0 | 21.1 | 19.2 | - | 0 | 17% | -0.34 | 36% | -0.75 | 17% |
| prophet | 178 | 0 | 21.1 | 19.2 | - | 0 | 17% | -0.35 | 33% | -0.74 | 14% |
| threshold80 | 178 | 0 | 4.9 | 3.3 | - | 0 | - | - | - | - | - |
| threshold90 | 178 | 0 | 2.8 | 1.0 | - | 0 | - | - | - | - | - |

## Jobs: predicting a missed deadline

Jobs: 436 missed their deadline (DeadlineExceeded), 344 finished in time (Complete), 1 excluded (still running, no outcome recorded, or too few readings).

| Method | Late jobs | Missed | Warning before deadline (median s) | False-alarm jobs | False-alarm rate | No forecast (too little data) | Accuracy @50% time | Accuracy @75% time | Finish-time error @50% (median s) |
|---|---|---|---|---|---|---|---|---|---|
| linear | 436 | 0 | 218 | 211/344 | 22% | 16% | 88% (415TP 21FN 76FP 268TN) | 92% (428TP 8FN 55FP 289TN) | 7 |
| prophet | 436 | 0 | 218 | 211/344 | 22% | 16% | 88% (415TP 21FN 76FP 268TN) | 92% (429TP 7FN 55FP 289TN) | 7 |
| threshold80 | 436 | 0 | 43 | 316/344 | 13% | - | 44% (0TP 436FN 0FP 344TN) | 44% (0TP 436FN 0FP 344TN) | - |

TP = late job flagged, FN = late job not flagged, FP = on-time job flagged, TN = on-time job not flagged.

## How to read this

- Storage bias > 0 means the forecast was late. Evictions happen at the kubelet's next disk check after the limit is crossed, so forecasts are expected to be slightly early (negative bias).
- Disk readings are re-measured only every ~70-100 s, so storage predictions are coarser than memory ones.
- 'No forecast' is the share of decisions where the forecaster refused (too few readings). A refusal counts as 'no alert', so read false-alarm rates together with it.
- Controlled workloads in one kind cluster: these results demonstrate the method, not general accuracy.

# Storage and Job-deadline evaluation: namespace `predict-lab`

Generated 2026-11-18 11:49 UTC; data from 2026-10-26T04:27 until 2026-11-18T12:00 (UTC). Each decision used only readings available at that moment.

Excluded because they started before the evaluation window: jobs: 788.

## Jobs: predicting a missed deadline

Jobs: 521 missed their deadline (DeadlineExceeded), 452 finished in time (Complete), 4 excluded (still running, no outcome recorded, or too few readings).

| Method | Late jobs | Missed | Warning before deadline (median s) | False-alarm jobs | False-alarm rate | No forecast (too little data) | Accuracy @50% time | Accuracy @75% time | Finish-time error @50% (median s) |
|---|---|---|---|---|---|---|---|---|---|
| linear | 521 | 2 | 218 | 250/452 | 21% | 16% | 87% (495TP 26FN 96FP 356TN) | 92% (508TP 13FN 68FP 384TN) | 7 |
| linear+2x | 521 | 2 | 198 | 192/452 | 16% | 16% | 88% (493TP 28FN 89FP 363TN) | 92% (501TP 20FN 62FP 390TN) | 7 |
| threshold80 | 521 | 0 | 43 | 395/452 | 12% | - | 46% (0TP 521FN 0FP 452TN) | 46% (0TP 521FN 0FP 452TN) | - |

TP = late job flagged, FN = late job not flagged, FP = on-time job flagged, TN = on-time job not flagged.

## How to read this

- Storage bias > 0 means the forecast was late. Evictions happen at the kubelet's next disk check after the limit is crossed, so forecasts are expected to be slightly early (negative bias).
- Disk readings are re-measured only every ~70-100 s, so storage predictions are coarser than memory ones.
- 'No forecast' is the share of decisions where the forecaster refused (too few readings). A refusal counts as 'no alert', so read false-alarm rates together with it.
- Controlled workloads in one kind cluster: these results demonstrate the method, not general accuracy.

# Calibrated time-to-limit ranges (linear), target 80%

Calibration: `/home/sunbeam/k8s-baseline-20261015-1514/phase4/eval-mem-cal/decisions.csv` (8681 forecasts of real crashes). Test (unseen): `/home/sunbeam/k8s-baseline-20261015-1514/phase4/eval-mem-test/decisions.csv`.

## Corrections learned from the calibration set (error = true time - forecast time)

| Forecast horizon | Errors used | Range around the forecast (s) |
|---|---|---|
| 0-5 min | 1891 | -4 to -1 |
| 5-10 min | 1918 | -5 to -0 |
| 10-20 min | 3608 | -9 to +5 |
| 20-30 min | 1264 | -8 to -0 |

## On the test set

| Forecast horizon | Forecasts | Original range: coverage | median width (s) | Calibrated: coverage | median width (s) |
|---|---|---|---|---|---|
| 0-5 min | 1595 | 80% | 7 | 80% | 4 |
| 5-10 min | 1633 | 77% | 7 | 79% | 4 |
| 10-20 min | 3041 | 48% | 7 | 79% | 14 |
| 20-30 min | 1030 | 45% | 8 | 74% | 7 |
| **all** | 7299 | 61% | 7 | 79% | 7 |

Coverage = share of forecasts whose true time to the crash fell inside the range (target 80%).
The guarantee assumes future errors resemble past ones; the test set checks whether they did.

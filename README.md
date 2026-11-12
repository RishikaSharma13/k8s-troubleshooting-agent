# Kubernetes Failure Prediction

[![tests](https://github.com/RishikaSharma13/k8s-troubleshooting-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/RishikaSharma13/k8s-troubleshooting-agent/actions/workflows/tests.yml)

Predicts **when** a Kubernetes workload will fail, early enough to act, and measures whether that prediction is
actually better than a simple threshold alert.

Three predictions, each scored against failures that Kubernetes itself recorded:

| Prediction | What grows | Limit | Failure recorded by Kubernetes |
|---|---|---|---|
| **Memory exhaustion** | container memory | memory limit | `OOMKilled` |
| **Local-storage exhaustion** | pod ephemeral storage | ephemeral-storage limit | pod `Evicted` |
| **Job deadline miss** | completed Job items | completions, before the deadline | Job `DeadlineExceeded` |

**Headline, on data the system had never been evaluated on:**

- **Memory (437 OOMKills):** warned **18.5–29 minutes** before a crash, against **3.6–7.9 minutes** for an 80% threshold
  alert; **0 missed**; **0% false alarms** on a steady workload where the 80% alert fired **51%** of the time.
- **Storage (178 evictions):** warned **21 minutes** before an eviction, against **4.9 minutes**; **0% false alarms**
  on a steady disk where the 80% alert fired **100%** of the time.
- **Jobs (780 jobs):** **88% correct** at the halfway point about whether a Job will miss its deadline, where a
  "running too long" alert cannot decide at all.

> This is a learning and portfolio project, built and evaluated on controlled test workloads in a local
> [kind](https://kind.sigs.k8s.io/) cluster. It demonstrates the method; the numbers are not general accuracy rates.

---

## How it works

```mermaid
flowchart TD
    A[Test workloads in a kind cluster] --> B[metrics-server and kubelet<br/>measure memory and disk]
    B --> C[Collector, every 15 s<br/>read-only kubectl]
    C --> D[(PostgreSQL<br/>readings and recorded failures)]
    D --> E[Forecaster<br/>trend since last restart, extended to the limit]
    D --> F[Evaluation<br/>replays history, scores every decision]
    E --> G[Dashboard and demo script]
    F --> G
```

1. **Collect.** Every 15 seconds the collector reads memory from the Kubernetes Metrics API, local-storage use from the
   kubelet, and Job progress, and records every failure (OOMKill, eviction, deadline miss) with its exact time.
2. **Forecast.** Using only readings since the container last restarted, it fits the trend (linear regression or
   [Prophet](https://facebook.github.io/prophet/)) and extends it to find when the limit will be reached.
   Example: 90 MiB now, growing 12 MiB/min, limit 256 MiB → (256 − 90) / 12 ≈ 13.8 minutes.
3. **Evaluate.** A rolling-origin backtest replays the stored history: at each decision point every method decides
   using only data available at that moment, then is scored against what actually happened.
4. **Show.** A read-only HTML dashboard shows live forecasts next to what a threshold alert would be doing, plus the
   evaluation results.

5. **Act and verify.** A predicted crash becomes an incident with a recommended action. A person approves one exact
   action; it runs only after a server-side dry run, and the system then checks by itself whether the crash was
   avoided. Nothing runs automatically. See [Closing the loop](#closing-the-loop-predict-approve-execute-verify).

---

## Results

All final numbers come from data the system had **never been evaluated on**, up to 26 Oct 2026 04:27 UTC:
memory from 21 Oct 05:51 UTC (after the first evaluation), storage and Jobs from 22 Oct 07:25 UTC (after the last
design change). Lives or Jobs that started before a window and failed inside it are excluded, so no history is cut
mid-way. Reports: [`docs/results/`](docs/results/).

### Memory (OOMKill): 437 OOMKills (292 `leak-steady`, 145 `leak-slow`)

| Method | Warning before OOMKill (median, steady / slow) | Missed | False alarms on steady workload (`flat-hot`) |
|---|---|---|---|
| Linear forecast | **18.5 / 29.2 min** | 0 | **0%** |
| Prophet forecast | 18.5 / 29.3 min | 0 | 0% |
| Threshold 80% | 3.6 / 7.9 min | 0 | **51%** (switched on and off 888 times in 5 days) |
| Threshold 90% | 1.6 / 3.6 min | 0 | 0% |

The forecast's time-to-limit was accurate to a few seconds (bias +0.03 to +0.07 min at 10 and 5 minutes before the
OOMKill). The first evaluation (32 OOMKills, 21 Oct) gave the same picture: 18.5 / 29.4 min vs 3.6 / 7.7 min.

### Storage eviction: 178 evictions

| Method | Warning before eviction (median) | Missed | False alarms on steady disk (`disk-flat`) | No forecast (refusals) |
|---|---|---|---|---|
| Linear / Prophet forecast | **21.1 min** | 0 | **0%** | 1% on `disk-flat`; 17% on `disk-leak` (first minutes of each fill) |
| Threshold 80% | 4.9 min | 0 | **100%** | – |
| Threshold 90% | 2.8 min | 0 | 0% | – |

Forecasts came out slightly early (bias −0.34 to −0.75 min), the safe direction: the kubelet evicts at its next disk
check after the limit is crossed.

### Job deadlines: 780 Jobs (436 late, 344 on time)

| Method | Warning before deadline (median) | Accuracy at 50% of allowed time | Accuracy at 75% |
|---|---|---|---|
| Linear / Prophet forecast | **218 s** | **88%** (415 of 436 late Jobs flagged, 76 false flags) | **92%** |
| "Running past 80% of time" alert | 43 s | 44% (cannot flag anything yet) | 44% |

At the halfway point the predicted finish time was accurate to about 7 seconds (median).

### Honest limitations

- **Prediction intervals are overconfident:** the actual time fell inside the 80% interval only 45–93% of the time
  for memory and 14–36% for storage.
- **Early Job alerts are noisy:** 61% of on-time Jobs received at least one "late" alert in their first few
  decisions, before enough items were done. Requiring two consecutive "late" predictions is the obvious next step.
- **Prophet was no better than linear regression** (slightly worse calibrated), because the test leaks are linear by
  design. Prophet's strengths (daily patterns, changing trends) are not exercised here.
- **On the steady memory workload, "memory stays the same" predicted 5 minutes ahead slightly better** than the
  forecasts (1.8 vs 2.6 MiB error), although the forecasts raised no false alarms.
- **Disk is re-measured only every ~70–100 s** by the kubelet, so storage predictions are coarser than memory ones.
- **The startup-step fix was not re-tested on unseen data:** `disk-flat` did not restart inside the window.
- **Controlled workloads in one local cluster:** this demonstrates the method, not production accuracy.

---

## Closing the loop: predict, approve, execute, verify

The forecasts drive a human-approved remediation loop, tested in its own namespace (`remediate-lab`) so it never
disturbs the evaluation workloads:

1. **Predict.** A watcher forecasts each workload every minute. When a memory crash is predicted within 30 minutes it
   raises **one incident per container life** (the database refuses duplicates), with the predicted crash time, a
   recommended action and the forecast's own numbers as reasoning. No invented confidence score.
2. **Approve.** A person approves **one exact action** (`/actions/approve`); anything else is refused at execution.
3. **Execute safely.** Only `RESTART_DEPLOYMENT` and `INCREASE_MEMORY_LIMIT` exist; the namespace must carry the
   label `reliability-agent/remediation=allowed` (read live); the exact command runs first as a **server-side dry
   run**; the rollout is awaited; before and after snapshots are kept. **Every attempt is recorded**, including
   refusals and failures. A memory increase is +50% of the limit actually read (capped at 1 GiB), refused rather than
   guessed.
4. **Verify.** After the predicted crash time (+5 minutes) each incident gets a verdict, at deployment level and from
   recorded data only: **AVOIDED / NOT_AVOIDED** if acted on, **CONFIRMED / NOT_CONFIRMED** if not, and
   **UNVERIFIABLE** when there is no data (for example the collector was not running): no claim either way.

### Live results (12 Nov 2026)

| Verdict | Incidents | What it shows |
|---|---|---|
| CONFIRMED | **84** | Unacted predictions that came true: median error **16 s** (max 168 s), made a median **18.1 min** ahead |
| NOT_CONFIRMED | **0** | No prediction with data failed to come true |
| AVOIDED | **1** | An approved restart, run 15.3 min before the predicted crash: the crash did not happen |
| UNVERIFIABLE | **7** | No data (laptop off), or one action taken before executions were recorded |

These are predictions made **in advance and automatically**, not a backtest. Honest notes: AVOIDED rests on a single
example so far; and a restart avoids an uncontrolled crash but **does not fix a leak**, as the recommendation itself
says. The first live restart was **refused** by the executor's own dry run (`kubectl rollout restart` has no
`--dry-run`); it is now done as the equivalent dry-runnable `kubectl patch`, and a test checks every command against
the real kubectl.

---

## Running it

### Prerequisites

- Docker, [kind](https://kind.sigs.k8s.io/) and kubectl
- [metrics-server](https://github.com/kubernetes-sigs/metrics-server) in the cluster (this project used `--metric-resolution=15s`)
- Python **3.10** and a PostgreSQL server (here: a Docker container named `k8s-agent-db`)

### Setup

```bash
git clone git@github.com:RishikaSharma13/k8s-troubleshooting-agent.git
cd k8s-troubleshooting-agent
python3.10 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt

# Databases: one for data, one for tests only
docker exec k8s-agent-db psql -U postgres -c "CREATE DATABASE k8s_agent"
docker exec k8s-agent-db psql -U postgres -c "CREATE DATABASE k8s_agent_test"
export DATABASE_URL="postgresql://postgres:password@localhost:5432/k8s_agent"
PYTHONPATH=backend alembic upgrade head

# Test workloads (namespace predict-lab)
kubectl apply -f docs/testing/predict-lab/predict-lab.yaml
kubectl apply -f docs/testing/predict-lab/predict-lab-more.yaml
```

### Collect

Runs as a background user service, so closing the terminal does not stop it:

```bash
systemd-run --user --unit=k8s-collector --working-directory="$PWD" \
  --setenv=DATABASE_URL="$DATABASE_URL" --setenv=PYTHONPATH=backend --setenv=PATH="$PATH" \
  -p StandardOutput=append:"$PWD/collector.log" -p StandardError=append:"$PWD/collector.log" \
  "$PWD/venv/bin/python" -m app.collector.run --namespace default --namespace predict-lab --interval 15

systemctl --user status k8s-collector      # check
systemctl --user stop k8s-collector        # stop
```

### Forecast, evaluate, show

```bash
# Live forecast for one workload
PYTHONPATH=backend python -m app.predictor.memory_forecast --namespace predict-lab --deployment leak-steady

# Backtests (read-only). These reproduce the final results exactly:
PYTHONPATH=backend python -m app.predictor.evaluate --namespace predict-lab \
  --since 2026-10-21T05:51 --until 2026-10-26T04:27 --out out/memory
PYTHONPATH=backend python -m app.predictor.evaluate_signals --namespace predict-lab \
  --since 2026-10-22T07:25 --until 2026-10-26T04:27 --out out/signals

# Dashboard (one self-contained HTML file; open it in a browser)
PYTHONPATH=backend python -m app.predictor.dashboard --out dashboard.html \
  --report docs/results/final-memory.md --signals-report docs/results/final-storage-jobs.md
```

### Close the loop

```bash
# Watcher (raises incidents and verifies them) and API, as background services
systemd-run --user --unit=k8s-watch --working-directory="$PWD" --setenv=DATABASE_URL="$DATABASE_URL" \
  --setenv=PYTHONPATH=backend "$PWD/venv/bin/python" -m app.remediation.watch --namespace remediate-lab
systemd-run --user --unit=k8s-api --working-directory="$PWD" --setenv=DATABASE_URL="$DATABASE_URL" \
  --setenv=PYTHONPATH=backend --setenv=PATH="$PATH" \
  "$PWD/venv/bin/python" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

# Approve and execute one incident (ID from the incidents table)
curl -X POST "localhost:8000/api/v1/actions/approve?incident_id=$INC&action=RESTART_DEPLOYMENT"
curl -X POST "localhost:8000/api/v1/remediation/execute?incident_id=$INC&action=RESTART_DEPLOYMENT"

# Verdicts so far
PYTHONPATH=backend python -m app.remediation.verify --report
```

### Tests

```bash
DATABASE_URL="postgresql://postgres:password@localhost:5432/k8s_agent_test" \
  PYTHONPATH=backend python -m pytest backend/tests -q
```

Tests that touch the database run **only** against a database whose name ends in `_test`; otherwise they are
skipped, so tests can never change real data.

---

## Project structure

```
backend/app/collector/     memory_collector.py, signals.py, run.py   - collection every 15 s
backend/app/predictor/     memory_forecast.py                        - time-to-limit forecasts
                           evaluate.py, evaluate_signals.py          - backtests
                           dashboard.py                              - read-only HTML dashboard
backend/app/remediation/   watch.py, actions.py, verify.py           - incidents, safe execution, verification
backend/app/api/           predictor_routes.py                       - incidents, approval, remediation API
backend/app/models/        database.py                               - tables
backend/tests/             automated tests (conftest.py guards the real database)
alembic/versions/          database migrations
docs/testing/predict-lab/  test workloads with known behaviour
docs/testing/remediate-lab/ the workload that remediation acts on (kept separate)
docs/results/              committed evaluation reports
```

---

## Design decisions and lessons

- **Measure before claiming.** The first version showed a "77% escalation risk" for a workload that never crashed;
  the number came from a formula, not a measurement. The project was rebuilt around predictions scored against real,
  recorded failures.
- **Never estimate missing data.** A missing reading is skipped. The old collector filled gaps with guesses that
  looked exactly like measurements.
- **Store the measurement's own timestamp,** and let the database reject duplicate readings.
- **Check the data before modelling it.** A pilot showed the kubelet re-measures disk only every ~70–100 s although
  its timestamp updates every few seconds; without that check, storage forecasts would have been quietly wrong.
- **Decide on development data, test on unseen data.** A storage false-alarm problem (a one-off startup write read as
  a trend) was diagnosed on early data; the fix is evaluated only on data collected afterwards.
- **Report refusals.** One fix looked like a big improvement until it turned out the forecaster was refusing to answer;
  every report now shows how often it declined.
- **Approval binds one exact action.** Execution refuses any other action.
- **Dry run first, and let it say no.** The executor's dry run refused the first live restart because of a bug of mine
  (an unsupported flag); nothing changed, and the refusal was recorded with its evidence.
- **No data, no verdict.** When the collector was not running, verification says UNVERIFIABLE rather than reporting
  that a predicted crash "did not happen".
- **Remove unsafe paths; deploy only what is merged.** The old executor (no dry run, guessed limits, no records) ran
  once because a service was restarted before the new code was merged. It was deleted, not kept beside the new one,
  and every deployment now starts with a check that the merged code is really there.

---

## What's next

- More approved actions, so that AVOIDED rests on more than one example; then low-risk automatic actions.
- Test on workloads with daily patterns and changing growth, where Prophet could matter.
- Calibrate prediction intervals.
- Count distinct incidents and alert volume, as a production system would need.

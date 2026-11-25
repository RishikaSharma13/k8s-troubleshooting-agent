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

Beyond prediction: a **human-approved remediation loop** (in a controlled experiment, 10 of 10 approved actions
prevented the predicted crash, while 10 of 10 left alone crashed), **calibrated 80% ranges** (79% coverage on unseen
data), and **Prometheus/Grafana integration**, where a "will fail within 15 minutes" alert runs beside the classic
"above 80%" one.

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

- **Prediction intervals were overconfident:** the actual time fell inside the 80% interval only 45–93% of the time
  for memory and 14–36% for storage. Memory ranges are now calibrated (79% on unseen data, see
  [Measured improvements](#measured-improvements-on-new-data)); storage ranges are not yet.
- **Early Job alerts are noisy:** 61% of on-time Jobs received at least one "late" alert in their first few
  decisions. Requiring two consecutive "late" predictions removed about a quarter of them; most persist, so the
  cause lies elsewhere (see [Measured improvements](#measured-improvements-on-new-data)).
- **Prophet was no better than linear regression** (slightly worse calibrated), because the test leaks are linear by
  design. Prophet's strengths (daily patterns, changing trends) are not exercised here.
- **On the steady memory workload, "memory stays the same" predicted 5 minutes ahead slightly better** than the
  forecasts (1.8 vs 2.6 MiB error), although the forecasts raised no false alarms.
- **Disk is re-measured only every ~70–100 s** by the kubelet, so storage predictions are coarser than memory ones.
- **The startup-step fix was not re-tested on unseen data:** `disk-flat` did not restart inside the window.
- **Realistic patterns expose limits:** cyclic memory defeats a 15-minute look-back (fixed by 45 minutes on a small
  sample), and random spikes make crashes come earlier than any trend predicts (see
  [Realistic workloads](#realistic-workloads-where-the-method-holds-and-where-it-breaks)).
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
4. **Verify.** After the predicted crash time (+5 minutes) each incident gets a verdict from recorded data only:
   **AVOIDED / NOT_AVOIDED** if acted on (judged on the predicted container life; a later crash of a *new* life is
   recorded separately), **CONFIRMED / NOT_CONFIRMED** if not, and **UNVERIFIABLE** when there is no data, the
   readings stopped or paused before the window closed (e.g. the laptop slept), or the predicted life ended for
   another reason first (e.g. a runtime restart): no claim either way. A crash that came after a pause stays
   CONFIRMED, but its timing is kept out of the error statistics.

**Approve with a button.** `http://127.0.0.1:8080/ui` (opened by `scripts/start.sh`) shows the dashboard with a live *Incidents awaiting approval*
panel: one click (with confirmation) approves and executes through the same safe path, and the command, dry run and
before → after appear on the page. POSTs from other websites are refused.

### Controlled experiment (16 Nov 2026)

To show that the action, not luck, prevents the crash, alternate predicted incidents were acted on (planned restart)
or deliberately left alone, ten of each, through the normal approval and safety path:

| Group | Incidents | Verdict |
|---|---|---|
| Treated (acted on) | 10 | **AVOIDED 10 of 10**: actions ran a median 17.8 min before the predicted crash |
| Control (left alone) | 10 | **CONFIRMED 10 of 10**: each crashed as predicted |

If acting made no difference, a split this clean would happen by chance about 1 time in 92,000 (two-sided Fisher
exact test). **A measured side-finding:** in 8 cases the restarted container crashed again a median **+3.2 min** after
the original predicted time. A restart prevents the uncontrolled crash at that moment, but **it does not fix a leak**.

On 17 Nov **both actions were tested live with the approval buttons**: a planned restart and a memory-limit increase
(256 → 384 MiB, +50% of the limit read from the cluster). Each was **AVOIDED**, bringing the total to **13 AVOIDED**;
the limit was then reset to 256 MiB.

### Live prediction accuracy

Re-verified on 17 Nov 2026 under the final rules: **155** cleanly observed unacted predictions came true with a
median error of **14 s** and a **maximum of 66 s**, made about **18 min** ahead; 2 more came true after a laptop pause
(their timing is not counted); **0** predictions failed to come true; 14 were UNVERIFIABLE (no data, a pause before the
window closed, or the life ended for another reason). These are predictions made **in advance and automatically**,
not a backtest.

Honest notes: one workload type, one action, a synthetic linear leak. The first live restart was **refused** by the
executor's own dry run (`kubectl rollout restart` has no `--dry-run`); it is now the equivalent dry-runnable
`kubectl patch`, and a test checks every command against the real kubectl. Verification was corrected three times
after surprising numbers were investigated (a restarted container's later crash; a restart for an unrelated reason; a
laptop suspend 49 s before a predicted crash), and all verdicts were recomputed each time. Every outlier traced back
to how results were judged, not to the forecast.

---

## Monitoring with Prometheus and Grafana

The forecasts are published as **Prometheus metrics**, so the same comparison can run in the tools teams already use.

- **A read-only exporter** (`app/metrics/exporter.py`) serves only `GET /metrics` and `/health`. It runs in the
  cluster as its own Deployment, with no Kubernetes API token, and Prometheus finds it through a **ServiceMonitor**
  created by the Helm chart (no fixed address).
- **Metrics:** `k8s_failure_seconds_to_limit` (with the calibrated `_lower`/`_upper`), `k8s_failure_memory_usage_ratio`,
  `k8s_failure_forecast_status`, `k8s_failure_incidents` (by verdict), `k8s_failure_executions` (by result).
- **kube-prometheus-stack** installed with Helm, pinned to a release at least 14 days old
  (`docs/monitoring/pick_chart_version.py`); values in `docs/monitoring/kube-prometheus-stack-values.yaml`.
- **Two alert rules side by side** (`docs/monitoring/forecast-rules.yaml`): `MemoryLimitPredictedSoon`
  (`seconds_to_limit < 900`) and `MemoryAbove80Percent` (`usage_ratio > 0.8`), both with `for: 1m`.
- **A Grafana dashboard** provisioned automatically (`docs/monitoring/grafana-dashboard.yaml`): time to limit with the
  calibrated band, memory against the 80% line, alerts firing, incidents by verdict, executions by result.

Seen live: the forecast alert fires for leaking workloads while they are still well below 80%. On the busy but stable
`flat-hot`, whose memory moves between about 78% and 81%, the threshold rule never stays above 80% for a full minute,
so it keeps resetting instead of firing: `for:` hides the flapping, at the cost of reacting more slowly to real leaks.

Install with `scripts/setup-lab.sh --with-monitoring` (or the steps in that script); Grafana is then at
http://localhost:3000 and Prometheus at http://localhost:9090 (port-forwards started by `scripts/start.sh`).

---

## Measured improvements on new data

Both changes were decided **before** looking at the data they were scored on, and scored only on data collected after
the original evaluation (26 Oct onwards).

### Calibrated uncertainty ranges

The forecast's own "80%" ranges contained the real crash time far less than 80% of the time. **Split conformal
calibration** widens or narrows each range by the forecast's past errors, grouped by how far ahead it looks
(0–5, 5–10, 10–20, 20–30 min). Calibrated on the published window (21–26 Oct), tested on 26 Oct – 18 Nov
(7,299 forecasts of real crashes):

| | Original ranges | Calibrated |
|---|---|---|
| Coverage (target 80%) | 61% | **79%** |
| Median width | 7 s | 7 s |

Narrower close to the crash, wider only 10–20 min ahead; the 20–30 min group reached 74%, not 80%, because errors far
ahead differed between the two periods. The calibrated range is shown on the dashboard, in incident reasoning and as
Prometheus metrics. Report: [`docs/results/calibration-memory-2026-11-18.md`](docs/results/calibration-memory-2026-11-18.md).

### Debounced Job alerts

Alerting only after **two "late" predictions in a row**, on 973 Jobs (26 Oct – 18 Nov):

| | Plain forecast | Two in a row |
|---|---|---|
| On-time Jobs falsely flagged at least once | 250 of 452 (55%) | **192 of 452 (42%)** |
| Warning before a missed deadline (median) | 218 s | 198 s |
| Late Jobs missed | 2 of 521 | 2 of 521 |
| Correct at 50% / 75% of the time | 87% / 92% | 88% / 92% |

A quarter fewer false alarms at no cost in missed Jobs, but most false alarms persist across several decisions, so
debouncing alone is not the fix. Report: [`docs/results/jobs-debounce-2026-11-18.md`](docs/results/jobs-debounce-2026-11-18.md).

---

## Realistic workloads: where the method holds and where it breaks

All earlier results come from perfectly linear leaks. Three workloads in `pattern-lab` mimic real behaviour (parameters
fixed before collecting any data): **`cycle-leak`** (a 20-minute wave on a 2 MiB/min leak, like daily traffic,
compressed), **`noisy-leak`** (a 3 MiB/min leak with noise and rare +40 MiB spikes) and **`step-leak`** (+25 MiB
every 5 minutes, like a cache filling in batches).

The evaluation now also reports **sustained warning**: how long the alert was continuously on before the crash.
"Lead time" counts from the first alert, so alerts that flicker on and off early can inflate it; sustained warning
cannot be inflated.

**19–20 Nov, 15-minute look-back** (7–16 crashes per workload; report
[A](docs/results/pattern-lab-2026-11-20-A.md)):

| Workload | Sustained warning: forecast | Sustained warning: 80% alert | Timing 10 min before the crash | |
|---|---|---|---|---|
| `step-leak` (16 crashes) | **32.9 min** | 9.5 min | within 0.2 min; range right 100% | works well |
| `noisy-leak` (11) | **25.6 min** | 10.2 min | **5.6 min late**; range right ~30% | useful, but promises too much time |
| `cycle-leak` (7) | **0 min** | 5.6 min | 8.8 min early; range right 0% | **fails** |

On `cycle-leak`, lead time looked like 98 minutes, but the alert was **off at every crash**: early alerts on each
upswing, nothing when it mattered. Even the 80% alert did better there. Prophet matched linear regression everywhere.

**A pre-declared test of a 45-minute look-back** (more than two cycles; the cycle length is known from the workload's
design, so this is not tuned to results), scored on 20 Nov only and compared with 15 minutes on the same data (reports
[B](docs/results/pattern-lab-2026-11-20-B.md), [C](docs/results/pattern-lab-2026-11-20-C.md)):

| Workload | Sustained warning (15 → 45 min) | Timing 10 min before (15 → 45 min) |
|---|---|---|
| `cycle-leak` (3 crashes) | **0 → 34.6 min** | 9.5 min early → **1.0 min early**; range right 0% → 100% |
| `noisy-leak` (5) | 21.9 → 21.9 min | 6.2 → 9.1 min late |
| `step-leak` (7) | 32.8 → 32.0 min | 0.2 → 1.2 min early |

Findings: seeing more than two cycles **fixes the cyclic case** (indicative: 3 crashes); **spikes are a real limit**
of any trend-based forecast, so noisy workloads need a safety margin rather than a better trend; and no single
look-back suits every pattern, which points to an **adaptive look-back**. Prophet's built-in seasonalities are daily
and weekly, so a fair Prophet test on a 20-minute cycle would need a custom seasonality.

---

## Run it in Kubernetes with Helm

The whole system runs inside the cluster as one Helm release, from the chart in `charts/k8s-failure-agent`:

| Component | Kubernetes object | May do in the cluster |
|---|---|---|
| Database | StatefulSet with a 1 GiB volume (PostgreSQL 16) | nothing (no API token) |
| Migrations | Job, run as a Helm hook (post-install, pre-upgrade) | nothing |
| Collector | Deployment | read pods, Jobs and pod metrics in each watched namespace; optionally kubelet disk stats (`nodes/proxy`) |
| Watcher | Deployment | nothing: database only |
| API, approval page and executor | Deployment (+ dashboard sidecar), ClusterIP Service | patch Deployments **only in the remediation namespace**, and read that namespace's labels |
| Exporter | Deployment, ClusterIP Service, ServiceMonitor | nothing: database only |

How the chart protects the system:

- **Least privilege, verified by Kubernetes itself:** `kubectl auth can-i` confirms the collector cannot delete pods
  or read Secrets, the watcher cannot touch the cluster, and the only write anywhere is patching Deployments in
  `remediate-lab`. No wildcards, no Secrets access, no create or delete. Every container runs as non-root, with a
  read-only root filesystem and all capabilities dropped.
- **The database password is generated once** and reused through `lookup`, and kept on uninstall like the volume,
  so neither an upgrade nor a reinstall can lock the components out of their data (verified on kind).
- **Migrations run before every upgrade.** If they fail, the upgrade stops and the running version is untouched;
  this happened for real when an all-digit image tag (`4764173`) was read as a number. The tag is now always
  rendered as text, and the setup script tags images `sha-<commit>`.
- **`values.schema.json` rejects mistakes before anything renders:** a typo such as `collecter.nodeStats` (which Helm
  would otherwise silently ignore), wrong types, an untagged or `latest` database image, out-of-range ports.
- **`helm test`** checks a running release from inside the cluster: the API and exporter answer, and `/metrics`
  carries forecast metrics.

Every merge to `main` publishes a matched pair to GitHub's registry, after the tests and the Trivy gate: the image
`ghcr.io/rishikasharma13/k8s-failure-agent:sha-<commit>` and the chart
`oci://ghcr.io/rishikasharma13/charts/k8s-failure-agent`, version `<x.y.z>-sha<commit>`, whose default image is the
one from the same commit (never `latest`; the image's `org.opencontainers.image.revision` label names its commit).

```bash
# Install the published pair (choose a version from the repository's Packages page):
helm upgrade --install demo oci://ghcr.io/rishikasharma13/charts/k8s-failure-agent --version 0.2.0-sha<commit> \
  -n failure-agent --create-namespace --wait
# Or build from the working tree and load the image into kind:
docker build -t k8s-failure-agent:sha-$(git rev-parse --short HEAD) .
kind load docker-image k8s-failure-agent:sha-$(git rev-parse --short HEAD) --name aiops-lab
helm upgrade --install demo charts/k8s-failure-agent -n failure-agent --create-namespace \
  --set-string image.repository=k8s-failure-agent --set-string image.tag=sha-$(git rev-parse --short HEAD) --wait
helm test demo -n failure-agent --logs
scripts/start.sh                          # approval page http://127.0.0.1:8080/ui
```

**Moving in (24 Nov 2026).** The history built up by the earlier laptop setup was moved into the cluster with
`pg_dump`/`pg_restore` (PostgreSQL 15 to 16): both databases were backed up first, the components paused during the
copy, and every table was compared afterwards (identical row counts, from 289 incidents to 403,194 memory readings).
The full loop was then proven inside the cluster: a crash predicted for 12:29:34 was approved on the in-cluster page,
the least-privilege executor restarted the workload at 12:17:06 (12.5 minutes ahead), and the verifier judged it
**AVOIDED**.

---

## Running it

### Try it yourself (one command)

On a Linux machine with **Docker** (usable without sudo), **[kind](https://kind.sigs.k8s.io/)**, **kubectl**,
**Helm**, **git**, **Python 3.10+** and **curl** (plus **openssl** for monitoring):

```bash
git clone https://github.com/RishikaSharma13/k8s-troubleshooting-agent.git
cd k8s-troubleshooting-agent
scripts/setup-lab.sh --check              # what exists and what would be done; changes nothing
scripts/setup-lab.sh --with-monitoring    # build the lab (20-30 min the first time), then open it
```

It creates a kind cluster (`aiops-lab`, Kubernetes v1.34.0), metrics-server (v0.7.2), the three lab namespaces,
optionally Prometheus and Grafana (installed **before** the release, so its ServiceMonitor is created), builds the
image tagged `sha-<commit>`, and installs the Helm release. Every step skips what already exists, so it never changes
an existing setup. Then open **http://127.0.0.1:8080/ui**: within about 20 minutes the leaking workload gets a
predicted crash with an **Approve** button. With monitoring: Grafana at http://localhost:3000 (user `admin`, password
in `~/.k8s-failure-lab-grafana-admin`), Prometheus at http://localhost:9090. Add `--dev` for a local PostgreSQL and
Python environment, to run the tests.

Daily use: `scripts/start.sh` (after a reboot: starts the cluster if needed and opens the windows into it) and
`scripts/stop.sh` (closes them; the system keeps running in the cluster). To remove the lab completely, deliberately,
one command at a time: `helm uninstall demo -n failure-agent`, `kind delete cluster --name aiops-lab`.

### Running the components by hand (development)

The sections below run each component on your own machine, against a local database: how the system was developed
and evaluated, and still useful for experiments. The lab itself runs in the cluster (above).

### Prerequisites

- Docker, [kind](https://kind.sigs.k8s.io/) and kubectl
- [metrics-server](https://github.com/kubernetes-sigs/metrics-server) in the cluster (this project used `--metric-resolution=15s`)
- Python **3.10** and a PostgreSQL server (here: a Docker container named `k8s-agent-db`)

### Setup

```bash
git clone git@github.com:RishikaSharma13/k8s-troubleshooting-agent.git
cd k8s-troubleshooting-agent
python3.10 -m venv venv && source venv/bin/activate
pip install -r backend/requirements-dev.txt

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

# Approve with a button: open http://127.0.0.1:8000/ui (start the API with DASHBOARD_HTML=<generated dashboard>).
# The buttons call the same two endpoints below; POSTs from other websites are refused.
# Or approve and execute one incident by hand (ID from the incidents table)
curl -X POST "localhost:8000/api/v1/actions/approve?incident_id=$INC&action=RESTART_DEPLOYMENT"
curl -X POST "localhost:8000/api/v1/remediation/execute?incident_id=$INC&action=RESTART_DEPLOYMENT"

# Verdicts so far
PYTHONPATH=backend python -m app.remediation.verify --report

# Controlled experiment: act on every other predicted incident, leave the others as a control group.
# A bounded batch approval you start explicitly; actions go through the same API and safety checks.
PYTHONPATH=backend python -m app.remediation.experiment run --confirm --count 10 --namespace remediate-lab --log experiment.jsonl
PYTHONPATH=backend python -m app.remediation.experiment summary --log experiment.jsonl
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
                           experiment.py                             - treated vs control experiment
backend/app/predictor/     calibrate.py                              - conformal calibration of ranges
backend/app/metrics/       exporter.py                               - read-only Prometheus exporter
backend/app/api/           predictor_routes.py, ui_routes.py         - incidents, approval API, /ui approval page
backend/app/models/        database.py                               - tables
backend/tests/             automated tests (conftest.py guards the real database)
alembic/versions/          database migrations
docs/testing/predict-lab/  test workloads with known behaviour
docs/testing/remediate-lab/ the workload that remediation acts on (kept separate)
docs/testing/pattern-lab/  realistic patterns: cycles, noise and spikes, steps
docs/monitoring/           Prometheus/Grafana values, alert rules, dashboard
scripts/                   setup-lab.sh, start.sh, stop.sh
charts/k8s-failure-agent/  Helm chart: templates, values and schema, RBAC, helm test
Dockerfile                 one image for every component (non-root, pinned kubectl)
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
- **Measure the warning that is still on when it matters.** On cyclic memory, "time since the first alert" reported 98
  minutes of warning while the alert was off at every crash; "sustained warning" exposed it.
- **Make the safe path the default in packaging too.** The chart generates the database password once (an upgrade
  never changes it), runs migrations before upgrades (a failed one stopped a broken deploy for real), and validates
  values before rendering. Two bugs found on the way: duplicate pod labels that `helm lint` accepted (strict schema
  validation caught them) and an all-digit image tag read as a number.
- **Tests must not be able to touch real systems.** A setup test meant to fake a missing `helm` found the real one
  on a developer machine; the tests now run with a sealed search path of stand-ins.

---

## What's next

- CI that builds the image on every push, scans it (Trivy), publishes it and the chart, tagged by commit.
- GitOps: Argo CD deploying the chart from Git.
- Security hardening: NetworkPolicies, the restricted Pod Security Standard, an SBOM.
- An adaptive look-back (detect a repeating pattern, then look back over two of its cycles), re-tested on new data.
- A safety margin for spiky workloads, from their own calibrated errors; a fair Prophet test with a custom seasonality.

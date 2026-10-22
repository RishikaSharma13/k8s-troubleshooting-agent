"""Backtest for the two newer predictions (read-only): storage eviction and Job deadlines.

Storage (ephemeral_storage_bytes -> Evicted) reuses the memory backtest unchanged
in method: one life per pod, positives end Evicted, the never-evicted deployment
(disk-flat) supplies negatives, thresholds at 80/90% of the storage limit. Its
data-quality guard is looser because the kubelet re-measures disk only every
~70-100 s (Session 15 pilot).

Jobs (job_succeeded -> DeadlineExceeded / Complete) ask a different question:
will this Job finish before its deadline? At each origin:
- forecast (linear, prophet): fit completed items over time, extend it to the
  number of completions; alert if the predicted finish is after the deadline
  (or not reached at all within the horizon);
- threshold80: alert once 80% of the time to the deadline has passed and the
  Job is still unfinished (what a simple "running too long" alert does).
Positives are Jobs that ended DeadlineExceeded, negatives Jobs that ended
Complete. Metrics: detection of late Jobs, warning before the deadline, false
alarms on Jobs that finished in time, and the decision quality at fixed points
(50% and 75% of the allowed time).
"""
from __future__ import annotations

import argparse
import csv
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from app.predictor.evaluate import (
    Life, classify, evaluate_life, exclusion_reason, fmt, split_at_gaps, summarize, threshold_share_all_samples,
)
from app.predictor.memory_forecast import MiB, QualitySettings, forecast_series

STORAGE_SIGNAL, JOB_SIGNAL = "ephemeral_storage_bytes", "job_succeeded"
STORAGE_QUALITY = QualitySettings(min_samples=5, min_span_s=240, max_gap_s=300)
STORAGE_GAP_SPLIT_S = 300        # collector silence that splits a life (heartbeat is 180 s)
STORAGE_LOOKBACK_S = 30 * 60
STORAGE_HORIZON_S = 30 * 60
JOB_QUALITY = QualitySettings(min_samples=4, min_span_s=45, max_gap_s=200)
JOB_CHECKPOINTS = (0.5, 0.75)    # fractions of the allowed time
JOB_THRESHOLD = 0.8


# ------------------------------------------------------------------
# Loading
# ------------------------------------------------------------------
def _load(namespace: str, signal: str, until: Optional[datetime], session_factory: Optional[Callable]):
    from app.models.database import OutcomeEvent, SessionLocal, SignalSample
    session_factory = session_factory or SessionLocal
    db = session_factory()
    try:
        q = db.query(SignalSample).filter(SignalSample.namespace == namespace, SignalSample.signal == signal)
        o = db.query(OutcomeEvent).filter(OutcomeEvent.namespace == namespace)
        if until:
            q = q.filter(SignalSample.observed_at <= until)
            o = o.filter(OutcomeEvent.occurred_at <= until)
        samples = [(s.workload, s.object_name, s.observed_at, s.value, s.limit_value, s.deadline_at)
                   for s in q.order_by(SignalSample.object_name, SignalSample.observed_at).all()]
        outcomes = {(e.object_name, e.event_type): e.occurred_at for e in o.all()}
    finally:
        db.close()
    return samples, outcomes


def load_storage_lives(namespace: str, until: Optional[datetime] = None,
                       session_factory: Optional[Callable] = None) -> list[Life]:
    """One life per pod (an eviction ends the pod; its Deployment starts a new one), split at collector gaps."""
    samples, outcomes = _load(namespace, STORAGE_SIGNAL, until, session_factory)
    by_pod: dict[str, Life] = {}
    for workload, pod, t, value, limit, _ in samples:
        life = by_pod.setdefault(pod, Life(workload, pod, "", f"{pod}#1"))
        life.times.append(t); life.values.append(int(value)); life.limit_bytes = int(limit) if limit else None
    for pod, life in by_pod.items():
        evicted = outcomes.get((pod, "Evicted"))
        if evicted is not None:
            life.end_time, life.end_reason = evicted, "Evicted"
    return split_at_gaps(list(by_pod.values()), STORAGE_GAP_SPLIT_S)


@dataclass
class JobLife:
    workload: str
    job: str
    times: list[datetime] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    completions: float = 0.0
    deadline: Optional[datetime] = None
    outcome: Optional[str] = None          # DeadlineExceeded, Complete, None (unknown/still running)
    outcome_at: Optional[datetime] = None

    @property
    def start(self) -> datetime:
        return self.times[0]

    @property
    def case(self) -> str:
        if len(self.times) < JOB_QUALITY.min_samples or self.deadline is None:
            return "excluded"
        return {"DeadlineExceeded": "positive", "Complete": "negative"}.get(self.outcome, "excluded")


def load_job_lives(namespace: str, until: Optional[datetime] = None,
                   session_factory: Optional[Callable] = None) -> list[JobLife]:
    samples, outcomes = _load(namespace, JOB_SIGNAL, until, session_factory)
    jobs: dict[str, JobLife] = {}
    for workload, job, t, value, limit, deadline in samples:
        life = jobs.setdefault(job, JobLife(workload, job))
        life.times.append(t); life.values.append(value)
        life.completions, life.deadline = limit or 0.0, deadline
    for job, life in jobs.items():
        for event in ("DeadlineExceeded", "Complete"):
            if (job, event) in outcomes:
                life.outcome, life.outcome_at = event, outcomes[(job, event)]
    return list(jobs.values())


# ------------------------------------------------------------------
# Jobs: decisions
# ------------------------------------------------------------------
@dataclass
class JobDecision:
    workload: str
    job: str
    case: str
    origin: datetime
    elapsed_fraction: float
    method: str
    alert: bool
    status: str
    predicted_finish: Optional[datetime] = None
    deadline: Optional[datetime] = None
    outcome_at: Optional[datetime] = None


def evaluate_job(life: JobLife, methods: Sequence[str], step_s: float = 20.0) -> list[JobDecision]:
    """Decisions every step_s from the Job's start until it ended (or hit its deadline), using only past readings."""
    rows: list[JobDecision] = []
    allowed = (life.deadline - life.start).total_seconds()
    end = min(t for t in (life.outcome_at, life.deadline) if t is not None)
    t = life.start + timedelta(seconds=step_s)
    while t < end:
        idx = [i for i, ts in enumerate(life.times) if ts <= t]
        w_times, w_values = [life.times[i] for i in idx], [life.values[i] for i in idx]
        frac = (t - life.start).total_seconds() / allowed
        base = dict(workload=life.workload, job=life.job, case=life.case, origin=t, elapsed_fraction=frac,
                    deadline=life.deadline, outcome_at=life.outcome_at)
        horizon = max(60.0, (life.deadline - t).total_seconds() + 300)
        for m in methods:
            r = forecast_series(w_times, w_values, int(life.completions), method=m, horizon_s=horizon,
                                step_s=5.0, quality=JOB_QUALITY, scale=1.0)
            if r.status == "insufficient_data":
                rows.append(JobDecision(method=m, alert=False, status=r.status, **base))
                continue
            finish = r.window_end + timedelta(seconds=r.eta_s) if r.eta_s is not None else None
            late = finish is None or finish > life.deadline
            rows.append(JobDecision(method=m, alert=late, status="late" if late else "on_time",
                                    predicted_finish=finish, **base))
        alert = frac >= JOB_THRESHOLD
        rows.append(JobDecision(method=f"threshold{int(JOB_THRESHOLD * 100)}", alert=alert,
                                status="alert" if alert else "ok", **base))
        t += timedelta(seconds=step_s)
    return rows


def summarize_jobs(rows: Sequence[JobDecision]) -> dict:
    out = {}
    for m in sorted({r.method for r in rows}):
        rs = [r for r in rows if r.method == m]
        jobs = {}
        for r in rs:
            jobs.setdefault((r.job, r.case), []).append(r)
        pos = {k: sorted(v, key=lambda r: r.origin) for k, v in jobs.items() if k[1] == "positive"}
        neg = {k: sorted(v, key=lambda r: r.origin) for k, v in jobs.items() if k[1] == "negative"}
        warn, missed = [], 0
        for v in pos.values():
            first = next((r for r in v if r.alert), None)
            if first is None:
                missed += 1
            else:
                warn.append((first.deadline - first.origin).total_seconds())
        false_jobs = sum(1 for v in neg.values() if any(r.alert for r in v))
        neg_rows = [r for v in neg.values() for r in v]
        res = {"late_jobs": len(pos), "on_time_jobs": len(neg), "missed": missed,
               "warning_s_median": statistics.median(warn) if warn else None,
               "false_alarm_jobs": false_jobs,
               "false_alarm_rate": (sum(r.alert for r in neg_rows) / len(neg_rows)) if neg_rows else None}
        for cp in JOB_CHECKPOINTS:
            tp = fn = fp = tn = 0
            errs = []
            for (job, case), v in jobs.items():
                if case not in ("positive", "negative"):
                    continue
                r = min(v, key=lambda r: abs(r.elapsed_fraction - cp))
                if abs(r.elapsed_fraction - cp) > 0.1:
                    continue
                if case == "positive":
                    tp += r.alert; fn += not r.alert
                else:
                    fp += r.alert; tn += not r.alert
                    if r.predicted_finish is not None and r.outcome_at is not None:
                        errs.append(abs((r.predicted_finish - r.outcome_at).total_seconds()))
            n = tp + fn + fp + tn
            res[f"cp{int(cp * 100)}"] = {"tp": tp, "fn": fn, "fp": fp, "tn": tn,
                                         "accuracy": (tp + tn) / n if n else None,
                                         "finish_err_s_median": statistics.median(errs) if errs else None}
        out[m] = res
    return out


# ------------------------------------------------------------------
# Report
# ------------------------------------------------------------------
def write_report(path: Path, namespace: str, until: Optional[str], storage: Optional[tuple], jobs: Optional[tuple]) -> None:
    L = [f"# Storage and Job-deadline evaluation: namespace `{namespace}`", "",
         f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}; data until {until or 'now'}. "
         "Each decision used only readings available at that moment.", ""]
    if storage:
        lives, cases, summary, shares = storage
        L += ["## Storage: predicting eviction (ephemeral-storage limit)", "",
              "| Deployment | Lives | Evicted (scored) | Negative (scored) | Excluded |", "|---|---|---|---|---|"]
        for dep in sorted({l.deployment for l in lives}):
            ls = [l for l in lives if l.deployment == dep]
            n = lambda c: sum(1 for l in ls if cases[l.life_id] == c)
            L.append(f"| {dep} | {len(ls)} | {n('positive')} | {n('negative')} | {n('excluded')} |")
        excl = {}
        for l in lives:
            if cases[l.life_id] == "excluded":
                r = exclusion_reason(l, STORAGE_QUALITY.min_samples); excl[r] = excl.get(r, 0) + 1
        L += ["", "Excluded: " + (", ".join(f"{k}: {v}" for k, v in sorted(excl.items())) or "none") + ".", ""]
        for dep in sorted({d for d, _ in summary}):
            L += [f"### {dep}", "", "| Method | Evicted lives | Missed | Warning median (min) | Warning min (min) | "
                  "False-alarm rate | False-alarm episodes | Bias @10m (min) | Coverage @10m | Bias @5m | Coverage @5m |",
                  "|" + "---|" * 11]
            for (d, m), s in sorted(summary.items()):
                if d != dep or m == "persistence":
                    continue
                L.append("| " + " | ".join([m, str(s["oom_lives"]), str(s["missed"]), fmt(s["lead_min_median"]),
                         fmt(s["lead_min_min"]), fmt(s["false_alarm_rate"], pct=True), str(s["false_alarm_episodes"]),
                         fmt(s["cp600_bias_min"], "+.2f"), fmt(s["cp600_coverage"], pct=True),
                         fmt(s["cp300_bias_min"], "+.2f"), fmt(s["cp300_coverage"], pct=True)]) + " |")
            sh = {p: v for (d, p), v in shares.items() if d == dep}
            if sh:
                L += ["", "Threshold alert share over every sample: " +
                      ", ".join(f"threshold{p}: {fmt(v, pct=True)}" for p, v in sorted(sh.items())) + "."]
            L.append("")
    if jobs:
        lives, summary = jobs
        counts = {c: sum(1 for l in lives if l.case == c) for c in ("positive", "negative", "excluded")}
        L += ["## Jobs: predicting a missed deadline", "",
              f"Jobs: {counts['positive']} missed their deadline (DeadlineExceeded), {counts['negative']} finished "
              f"in time (Complete), {counts['excluded']} excluded (still running, no outcome recorded, or too few readings).", "",
              "| Method | Late jobs | Missed | Warning before deadline (median s) | False-alarm jobs | False-alarm rate | "
              "Accuracy @50% time | Accuracy @75% time | Finish-time error @50% (median s) |", "|" + "---|" * 9]
        for m, s in summary.items():
            c50, c75 = s["cp50"], s["cp75"]
            L.append("| " + " | ".join([m, str(s["late_jobs"]), str(s["missed"]), fmt(s["warning_s_median"], ".0f"),
                     f"{s['false_alarm_jobs']}/{s['on_time_jobs']}", fmt(s["false_alarm_rate"], pct=True),
                     f"{fmt(c50['accuracy'], pct=True)} ({c50['tp']}TP {c50['fn']}FN {c50['fp']}FP {c50['tn']}TN)",
                     f"{fmt(c75['accuracy'], pct=True)} ({c75['tp']}TP {c75['fn']}FN {c75['fp']}FP {c75['tn']}TN)",
                     fmt(c50["finish_err_s_median"], ".0f")]) + " |")
        L += ["", "TP = late job flagged, FN = late job not flagged, FP = on-time job flagged, TN = on-time job not flagged.", ""]
    L += ["## How to read this", "",
          "- Storage bias > 0 means the forecast was late. Evictions happen at the kubelet's next disk check after the "
          "limit is crossed, so forecasts are expected to be slightly early (negative bias).",
          "- Disk readings are re-measured only every ~70-100 s, so storage predictions are coarser than memory ones.",
          "- Controlled workloads in one kind cluster: these results demonstrate the method, not general accuracy.", ""]
    path.write_text("\n".join(L))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backtest storage-eviction and Job-deadline predictions (read-only).")
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--until", help="ISO time (UTC)")
    ap.add_argument("--what", choices=["storage", "jobs", "all"], default="all")
    ap.add_argument("--methods", default="prophet,linear")
    args = ap.parse_args(argv)
    until = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc) if args.until else None
    methods = [m for m in args.methods.split(",") if m]
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    storage = jobs = None
    if args.what in ("storage", "all"):
        lives = load_storage_lives(args.namespace, until)
        cases = classify(lives, STORAGE_QUALITY.min_samples)
        rows = []
        for life in lives:
            if cases[life.life_id] != "excluded":
                rows += evaluate_life(life, cases[life.life_id], 60.0, STORAGE_HORIZON_S, STORAGE_LOOKBACK_S,
                                      methods, (80, 90), quality=STORAGE_QUALITY, value_gap_s=STORAGE_QUALITY.max_gap_s)
        with open(out / "storage_decisions.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()) if rows else ["empty"]); w.writeheader()
            for r in rows:
                w.writerow(asdict(r))
        storage = (lives, cases, summarize(rows, STORAGE_HORIZON_S), threshold_share_all_samples(lives, cases, (80, 90)))
        print(f"storage: {len(lives)} lives, {len(rows)} decisions, {time.monotonic() - started:.0f}s", flush=True)
    if args.what in ("jobs", "all"):
        lives = load_job_lives(args.namespace, until)
        rows = []
        for life in lives:
            if life.case != "excluded":
                rows += evaluate_job(life, methods)
        with open(out / "job_decisions.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()) if rows else ["empty"]); w.writeheader()
            for r in rows:
                w.writerow(asdict(r))
        jobs = (lives, summarize_jobs(rows))
        print(f"jobs: {len(lives)} jobs, {len(rows)} decisions, {time.monotonic() - started:.0f}s", flush=True)
    write_report(out / "report.md", args.namespace, args.until, storage, jobs)
    print(f"done in {time.monotonic() - started:.0f}s: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

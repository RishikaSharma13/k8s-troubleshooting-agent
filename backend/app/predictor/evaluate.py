"""Backtest: how well do forecasts warn before OOMKills, compared with simple alerts?

Read-only. Loads memory_samples and container_terminations, replays history,
and at regular "origins" asks each method to decide using ONLY data up to that
moment. Writes a CSV of every decision and a Markdown report.

Definitions (fixed before looking at results):
- Life: a container's samples between two terminations. A life that ends in
  OOMKilled is a positive case. Deployments with no OOMKill at all (flat-hot)
  supply the negative cases. Lives still running when data ends are excluded,
  as are lives ending for other reasons and lives with fewer samples than the
  forecaster's minimum (too short for any method to be judged fairly). A
  collector gap longer than MAX_GAP_S (machine asleep, collector down) splits a
  life: the part before the gap is excluded, and an OOMKilled life that starts
  after a gap is excluded as incomplete; for never-OOMKilled workloads the pieces
  stay negative cases. All exclusions are counted in the report, never dropped silently.
- Methods: prophet / linear alert when the forecast's earliest time-to-limit
  is within the horizon (the production rule). thresholdNN alerts when current
  memory >= NN% of the limit. persistence ("stays where it is") is a floor for
  forecast error only.
- Lead time: OOMKill time minus the first alert in that life. Missed: no alert.
- Early alert: an alert when the OOMKill is more than the horizon away.
- False-alarm rate: share of negative-case origins with an alert.
- Time-to-limit error at checkpoints (10 and 5 min before OOMKill):
  predicted - actual; positive = forecast late (promised more time than there was).
- Interval coverage: actual time-to-OOMKill within [earliest, latest] at checkpoints.
- 5-min value error: |predicted - actual| memory 5 minutes ahead, MiB.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from app.predictor.memory_forecast import (
    DEFAULT_HORIZON_S, DEFAULT_LOOKBACK_S, MAX_GAP_S, MEMORY_QUALITY, MIN_SAMPLES, MiB, QualitySettings,
    forecast_series,
)

FORECAST_METHODS = ("prophet", "linear")
DEFAULT_THRESHOLDS = (80, 90)
CHECKPOINTS_S = (600, 300)
VALUE_AHEAD_S = 300
FAILURE_REASONS = ("OOMKilled", "Evicted")   # how a scored (positive) life ends
DROP_SPLIT_BYTES = 100 * MiB   # a sudden drop this large without a recorded termination = unrecorded restart


@dataclass
class Life:
    deployment: str
    pod: str
    container: str
    life_id: str
    times: list[datetime] = field(default_factory=list)
    values: list[int] = field(default_factory=list)
    limit_bytes: Optional[int] = None
    end_time: Optional[datetime] = None
    end_reason: Optional[str] = None      # OOMKilled, Completed, ..., "unrecorded_restart", "collector_gap", None = running
    starts_after_gap: bool = False

    @property
    def ends_in_oom(self) -> bool:
        return self.end_reason == "OOMKilled"

    @property
    def ends_in_failure(self) -> bool:
        return self.end_reason in FAILURE_REASONS


@dataclass
class OriginRow:
    deployment: str
    pod: str
    life_id: str
    case: str                       # positive | negative | excluded
    origin: datetime
    method: str
    alert: bool
    status: str
    value_mib: float
    limit_mib: float
    time_to_oom_s: Optional[float]  # positive cases only
    eta_s: Optional[float] = None
    eta_earliest_s: Optional[float] = None
    eta_latest_s: Optional[float] = None
    predicted_5min_mib: Optional[float] = None
    actual_5min_mib: Optional[float] = None


# ------------------------------------------------------------------
# Pure functions
# ------------------------------------------------------------------
def split_lives(deployment: str, pod: str, container: str, times: Sequence[datetime], values: Sequence[int],
                limit_bytes: Optional[int], terminations: Sequence[tuple[datetime, Optional[str]]],
                drop_split_bytes: int = DROP_SPLIT_BYTES) -> list[Life]:
    """Split one container's samples into lives.

    A sample belongs to segment k = number of terminations that finished before it;
    segment k ends with termination k (or is still running). Inside a segment, a
    sudden drop larger than drop_split_bytes marks an unrecorded restart.
    """
    terms = sorted(terminations)
    finish_times = [t for t, _ in terms]
    segments: dict[int, list[tuple[datetime, int]]] = {}
    for t, v in zip(times, values):
        segments.setdefault(bisect.bisect_left(finish_times, t), []).append((t, int(v)))

    lives: list[Life] = []
    for k in sorted(segments):
        seg_end = terms[k] if k < len(terms) else (None, None)
        current: list[tuple[datetime, int]] = []
        for t, v in segments[k]:
            if current and current[-1][1] - v > drop_split_bytes:
                lives.append(_life(deployment, pod, container, len(lives), current, limit_bytes,
                                   current[-1][0], "unrecorded_restart"))
                current = []
            current.append((t, v))
        lives.append(_life(deployment, pod, container, len(lives), current, limit_bytes, *seg_end))
    return split_at_gaps(lives)


def split_at_gaps(lives: Sequence[Life], max_gap_s: float = MAX_GAP_S) -> list[Life]:
    """Split lives wherever the collector was silent for more than max_gap_s."""
    out: list[Life] = []
    for life in lives:
        start = 0
        after_gap = life.starts_after_gap
        for i in range(1, len(life.times) + 1):
            gap = i < len(life.times) and (life.times[i] - life.times[i - 1]).total_seconds() > max_gap_s
            if i == len(life.times) or gap:
                piece = Life(life.deployment, life.pod, life.container, "", life.times[start:i], life.values[start:i],
                             life.limit_bytes, starts_after_gap=after_gap)
                if gap:
                    piece.end_time, piece.end_reason = life.times[i - 1], "collector_gap"
                else:
                    piece.end_time, piece.end_reason = life.end_time, life.end_reason
                out.append(piece)
                start, after_gap = i, True
    for n, life in enumerate(out):
        life.life_id = f"{life.pod}/{life.container}#{n + 1}"
    return out


def _life(deployment, pod, container, index, samples, limit_bytes, end_time, end_reason) -> Life:
    return Life(deployment, pod, container, f"{pod}/{container}#{index + 1}",
                times=[t for t, _ in samples], values=[v for _, v in samples],
                limit_bytes=limit_bytes, end_time=end_time, end_reason=end_reason)


def origin_indices(times: Sequence[datetime], step_s: float, end_time: Optional[datetime]) -> list[int]:
    """Sample indices used as decision points: at least step_s apart, strictly before the life's end."""
    out, last = [], None
    for i, t in enumerate(times):
        if end_time is not None and t >= end_time:
            break
        if last is None or (t - last).total_seconds() >= step_s - 1e-6:
            out.append(i)
            last = t
    return out


def value_at(times: Sequence[datetime], values: Sequence[int], at: datetime, max_gap_s: float = 45.0) -> Optional[float]:
    """Actual memory (MiB) at `at`, interpolated between neighbouring samples no more than max_gap_s apart."""
    xs = np.array([t.timestamp() for t in times])
    x = at.timestamp()
    if x < xs[0] or x > xs[-1]:
        return None
    j = int(np.searchsorted(xs, x))
    if xs[j] == x:
        return values[j] / MiB
    if xs[j] - xs[j - 1] > max_gap_s:
        return None
    return float(np.interp(x, xs[j - 1:j + 1], np.asarray(values[j - 1:j + 1], dtype=float))) / MiB


def evaluate_life(life: Life, case: str, step_s: float, horizon_s: float, lookback_s: float,
                  methods: Sequence[str], thresholds: Sequence[int],
                  quality: QualitySettings = MEMORY_QUALITY, value_gap_s: float = 45.0) -> list[OriginRow]:
    rows: list[OriginRow] = []
    limit_mib = (life.limit_bytes or 0) / MiB
    for i in origin_indices(life.times, step_s, life.end_time):
        t = life.times[i]
        lo = i
        while lo > 0 and (t - life.times[lo - 1]).total_seconds() < lookback_s:
            lo -= 1
        w_times, w_values = life.times[lo:i + 1], life.values[lo:i + 1]
        now_mib = life.values[i] / MiB
        tto = (life.end_time - t).total_seconds() if case == "positive" else None
        actual5 = value_at(life.times, life.values, t + timedelta(seconds=VALUE_AHEAD_S), value_gap_s)
        base = dict(deployment=life.deployment, pod=life.pod, life_id=life.life_id, case=case, origin=t,
                    value_mib=now_mib, limit_mib=limit_mib, time_to_oom_s=tto, actual_5min_mib=actual5)
        for m in methods:
            r = forecast_series(w_times, w_values, life.limit_bytes, method=m, horizon_s=horizon_s, quality=quality)
            rows.append(OriginRow(method=m, alert=r.status == "crossing_predicted", status=r.status,
                                  eta_s=r.eta_s, eta_earliest_s=r.eta_earliest_s, eta_latest_s=r.eta_latest_s,
                                  predicted_5min_mib=r.predicted_mib_at(VALUE_AHEAD_S), **base))
        for p in thresholds:
            alert = bool(limit_mib) and now_mib >= p / 100 * limit_mib
            rows.append(OriginRow(method=f"threshold{p}", alert=alert, status="alert" if alert else "ok", **base))
        rows.append(OriginRow(method="persistence", alert=False, status="value_only",
                              predicted_5min_mib=now_mib, **base))
    return rows


def _median(xs):
    return statistics.median(xs) if xs else None


def summarize(rows: Sequence[OriginRow], horizon_s: float) -> dict:
    """Per (deployment, method) metrics. Returns {(deployment, method): {...}}."""
    out: dict = {}
    keys = sorted({(r.deployment, r.method) for r in rows})
    for dep, m in keys:
        rs = [r for r in rows if r.deployment == dep and r.method == m]
        pos = [r for r in rs if r.case == "positive"]
        neg = [r for r in rs if r.case == "negative"]
        lives = sorted({r.life_id for r in pos})
        leads, misses = [], 0
        for life_id in lives:
            alerts = sorted((r for r in pos if r.life_id == life_id and r.alert), key=lambda r: r.origin)
            if alerts:
                leads.append(alerts[0].time_to_oom_s / 60)
            else:
                misses += 1
        early = sum(1 for r in pos if r.alert and r.time_to_oom_s > horizon_s)
        res = {"oom_lives": len(lives), "missed": misses,
               "lead_min_median": _median(leads), "lead_min_min": min(leads) if leads else None,
               "early_alert_origins": early,
               "negative_origins": len(neg),
               "false_alarm_rate": (sum(r.alert for r in neg) / len(neg)) if neg else None,
               "false_alarm_episodes": _episodes(neg)}
        for cp in CHECKPOINTS_S:
            errs, covered, no_est = [], 0, 0
            for life_id in lives:
                cand = [r for r in pos if r.life_id == life_id]
                if not cand:
                    continue
                r = min(cand, key=lambda r: abs(r.time_to_oom_s - cp))
                if abs(r.time_to_oom_s - cp) > 90:
                    continue
                if r.eta_s is None:
                    no_est += 1
                    continue
                errs.append((r.eta_s - r.time_to_oom_s) / 60)
                lo = r.eta_earliest_s if r.eta_earliest_s is not None else 0.0
                hi = r.eta_latest_s if r.eta_latest_s is not None else float("inf")
                covered += lo <= r.time_to_oom_s <= hi
            res[f"cp{cp}_n"] = len(errs)
            res[f"cp{cp}_no_estimate"] = no_est
            res[f"cp{cp}_bias_min"] = _median(errs)
            res[f"cp{cp}_abs_err_min"] = _median([abs(e) for e in errs])
            res[f"cp{cp}_coverage"] = (covered / len(errs)) if errs else None
        val = [abs(r.predicted_5min_mib - r.actual_5min_mib) for r in rs
               if r.predicted_5min_mib is not None and r.actual_5min_mib is not None]
        res["mae_5min_mib"] = (sum(val) / len(val)) if val else None
        res["mae_5min_n"] = len(val)
        out[(dep, m)] = res
    return out


def _episodes(rows: Sequence[OriginRow]) -> int:
    n, prev = 0, {}
    for r in sorted(rows, key=lambda r: (r.life_id, r.origin)):
        if r.alert and not prev.get(r.life_id, False):
            n += 1
        prev[r.life_id] = r.alert
    return n


# ------------------------------------------------------------------
# Database + report
# ------------------------------------------------------------------
def load_lives(namespace: str, since: Optional[datetime], until: Optional[datetime],
               session_factory: Optional[Callable] = None) -> list[Life]:
    from app.models.database import ContainerTermination, MemorySample, SessionLocal
    session_factory = session_factory or SessionLocal
    db = session_factory()
    try:
        q = db.query(MemorySample.deployment, MemorySample.pod, MemorySample.container,
                     MemorySample.metric_timestamp, MemorySample.memory_bytes, MemorySample.memory_limit_bytes
                     ).filter(MemorySample.namespace == namespace, MemorySample.deployment.isnot(None))
        tq = db.query(ContainerTermination.pod, ContainerTermination.container,
                      ContainerTermination.finished_at, ContainerTermination.reason
                      ).filter(ContainerTermination.namespace == namespace)
        if since:
            q, tq = q.filter(MemorySample.metric_timestamp >= since), tq.filter(ContainerTermination.finished_at >= since)
        if until:
            q, tq = q.filter(MemorySample.metric_timestamp <= until), tq.filter(ContainerTermination.finished_at <= until)
        samples = q.order_by(MemorySample.pod, MemorySample.container, MemorySample.metric_timestamp).all()
        terms = tq.all()
    finally:
        db.close()
    by_container: dict = {}
    for dep, pod, cont, ts, val, lim in samples:
        d = by_container.setdefault((dep, pod, cont), {"t": [], "v": [], "lim": lim})
        d["t"].append(ts); d["v"].append(val); d["lim"] = lim
    term_map: dict = {}
    for pod, cont, fin, reason in terms:
        term_map.setdefault((pod, cont), []).append((fin, reason))
    lives = []
    for (dep, pod, cont), d in by_container.items():
        lives += split_lives(dep, pod, cont, d["t"], d["v"], d["lim"], term_map.get((pod, cont), []))
    return lives


def classify(lives: Sequence[Life], min_samples: int = MIN_SAMPLES) -> dict[str, str]:
    """positive = ends in a failure (OOMKilled, Evicted); negative = the deployment never failed;
    excluded = everything else, including lives with fewer than min_samples samples (same rule for every method)."""
    oom_deps = {l.deployment for l in lives if l.ends_in_failure}

    def case(l: Life) -> str:
        if len(l.times) < min_samples:
            return "excluded"
        if l.ends_in_failure:
            return "excluded" if l.starts_after_gap else "positive"
        if l.deployment not in oom_deps and l.end_reason in (None, "collector_gap"):
            return "negative"
        return "excluded"
    return {l.life_id: case(l) for l in lives}


def exclusion_reason(life: Life, min_samples: int = MIN_SAMPLES) -> str:
    if len(life.times) < min_samples:
        return f"too short (<{min_samples} samples)"
    if life.ends_in_failure and life.starts_after_gap:
        return f"{life.end_reason} but starts after a collector gap (incomplete)"
    if life.end_reason == "collector_gap":
        return "ends at a collector gap"
    return life.end_reason or "still running"


def fmt(v, spec=".1f", pct=False):
    if v is None:
        return "-"
    return f"{100 * v:.0f}%" if pct else format(v, spec)


def threshold_share_all_samples(lives: Sequence[Life], cases: dict, thresholds: Sequence[int]) -> dict:
    """{(deployment, threshold): share of ALL negative-case samples at or above the threshold}.

    A robustness check on the origin-based false-alarm rate: if a workload oscillates with a
    period that lines up with the origin step, origins can all land on one phase (aliasing).
    """
    out = {}
    for dep in sorted({l.deployment for l in lives if cases[l.life_id] == "negative"}):
        neg = [l for l in lives if l.deployment == dep and cases[l.life_id] == "negative" and l.limit_bytes]
        values = [(v, l.limit_bytes) for l in neg for v in l.values]
        for p in thresholds:
            out[(dep, p)] = (sum(v >= p / 100 * lim for v, lim in values) / len(values)) if values else None
    return out


def write_report(path: Path, namespace: str, lives: Sequence[Life], cases: dict, summary: dict, params: dict,
                 thresholds: Sequence[int] = DEFAULT_THRESHOLDS) -> None:
    deps = sorted({d for d, _ in summary})
    L = [f"# Memory forecast evaluation: namespace `{namespace}`", "",
         f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}. Parameters: " +
         ", ".join(f"{k}={v}" for k, v in params.items()), "",
         "## Data", "", "| Deployment | Lives | OOMKilled (scored) | Negative (scored) | Excluded | Samples | From | To |",
         "|---|---|---|---|---|---|---|---|"]
    for dep in deps:
        ls = [l for l in lives if l.deployment == dep]
        ts = [t for l in ls for t in l.times]
        n = lambda c: sum(1 for l in ls if cases[l.life_id] == c)
        L.append(f"| {dep} | {len(ls)} | {n('positive')} | {n('negative')} | {n('excluded')} | {len(ts)} | "
                 f"{min(ts):%m-%d %H:%M} | {max(ts):%m-%d %H:%M} |")
    excl: dict[str, int] = {}
    for l in lives:
        if cases[l.life_id] == "excluded":
            excl[exclusion_reason(l)] = excl.get(exclusion_reason(l), 0) + 1
    L += ["", "Excluded lives: " + (", ".join(f"{k}: {v}" for k, v in sorted(excl.items())) or "none") + ".", ""]
    for dep in deps:
        methods = [m for d, m in summary if d == dep]
        L += [f"## {dep}", "",
              "| Method | OOM lives | Missed | Lead time median (min) | Lead time min (min) | Early alerts | "
              "False-alarm rate | False-alarm episodes | Bias @10m (min) | Abs err @10m | Coverage @10m | "
              "Bias @5m | Abs err @5m | Coverage @5m | 5-min value MAE (MiB) |",
              "|" + "---|" * 15]
        for m in methods:
            s = summary[(dep, m)]
            L.append("| " + " | ".join([
                m, str(s["oom_lives"]), str(s["missed"]), fmt(s["lead_min_median"]), fmt(s["lead_min_min"]),
                str(s["early_alert_origins"]), fmt(s["false_alarm_rate"], pct=True), str(s["false_alarm_episodes"]),
                fmt(s["cp600_bias_min"], "+.2f"), fmt(s["cp600_abs_err_min"], ".2f"), fmt(s["cp600_coverage"], pct=True),
                fmt(s["cp300_bias_min"], "+.2f"), fmt(s["cp300_abs_err_min"], ".2f"), fmt(s["cp300_coverage"], pct=True),
                fmt(s["mae_5min_mib"], ".2f")]) + " |")
        L.append("")
        shares = threshold_share_all_samples([l for l in lives if l.deployment == dep], cases, thresholds)
        if shares:
            L += ["Aliasing check: threshold alert share over every sample (not just decision points): " +
                  ", ".join(f"threshold{p}: {fmt(v, pct=True)}" for (_, p), v in shares.items()) + ".", ""]
    L += ["## How to read this", "",
          "- Bias > 0 means the forecast was late: it promised more time than there was.",
          "- Coverage is how often the actual time-to-OOMKill fell inside the forecast's interval.",
          "- '-' means not applicable or no data (thresholds and persistence give no time-to-limit).",
          "- Results come from synthetic, controlled workloads in one kind cluster; they demonstrate the "
          "method, they are not general accuracy rates.", ""]
    path.write_text("\n".join(L))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest memory forecasts against OOMKills (read-only).")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--out", required=True, help="output directory for report.md and decisions.csv")
    parser.add_argument("--since", help="ISO time (UTC), e.g. 2026-10-19T08:00")
    parser.add_argument("--until", help="ISO time (UTC)")
    parser.add_argument("--origin-step-s", type=float, default=60.0)
    parser.add_argument("--horizon-min", type=float, default=DEFAULT_HORIZON_S / 60)
    parser.add_argument("--methods", default=",".join(FORECAST_METHODS))
    parser.add_argument("--thresholds", default=",".join(map(str, DEFAULT_THRESHOLDS)))
    args = parser.parse_args(argv)

    parse = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if s else None
    methods = [m for m in args.methods.split(",") if m]
    thresholds = [int(x) for x in args.thresholds.split(",") if x]
    horizon_s = args.horizon_min * 60
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    lives = load_lives(args.namespace, parse(args.since), parse(args.until))
    cases = classify(lives)
    scored = [l for l in lives if cases[l.life_id] != "excluded"]
    print(f"{len(lives)} lives loaded, {len(scored)} scored; evaluating methods={methods} thresholds={thresholds}")
    rows: list[OriginRow] = []
    for n, life in enumerate(scored, 1):
        rows += evaluate_life(life, cases[life.life_id], args.origin_step_s, horizon_s, DEFAULT_LOOKBACK_S,
                              methods, thresholds)
        print(f"  [{n}/{len(scored)}] {life.life_id} ({cases[life.life_id]}, {len(life.times)} samples) "
              f"elapsed {time.monotonic() - started:.0f}s", flush=True)
    with open(out / "decisions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()) if rows else ["empty"])
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))
    summary = summarize(rows, horizon_s)
    params = {"origin_step_s": args.origin_step_s, "horizon_min": args.horizon_min,
              "lookback_min": DEFAULT_LOOKBACK_S / 60, "since": args.since, "until": args.until}
    write_report(out / "report.md", args.namespace, lives, cases, summary, params, thresholds)
    print(f"done in {time.monotonic() - started:.0f}s: {out / 'report.md'}, {out / 'decisions.csv'} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

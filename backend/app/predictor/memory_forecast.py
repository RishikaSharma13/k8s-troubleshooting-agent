"""Memory forecasting: when will a container reach its memory limit?

Replaces the old pod-name/percentage forecaster for the prediction workflow:

- Input is measured data only (memory_samples, bytes), per container, and only
  since the container's last termination: after an OOMKill the container
  restarts from near zero, so older samples belong to a different "life".
- Models are trained on request from stored data, never cached in memory.
- Output is an estimated time-to-limit with Prophet's prediction interval,
  not a heuristic confidence. Interval coverage is measured separately by
  the evaluation (a forecast is a hypothesis until validated).
- Too few or too-gappy samples give "insufficient_data", not a guess.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

import numpy as np
from loguru import logger

MiB = 1024 * 1024

# Data-quality guard (collector samples every ~15 s)
MIN_SAMPLES = 8          # ~2 minutes of readings
MIN_SPAN_S = 90.0
MAX_GAP_S = 90.0         # a longer silence means the series is not continuous

DEFAULT_LOOKBACK_S = 15 * 60
DEFAULT_HORIZON_S = 30 * 60
DEFAULT_STEP_S = 15.0
INTERVAL_WIDTH = 0.80    # Prophet prediction interval; coverage is measured, not assumed
# Prophet's default trend flexibility (0.05) is tuned for long business series. On a few
# minutes of one container's memory it read noise as trend changes: on synthetic flat
# series it raised 1/8 false alarms vs 0/8 at 0.01, with no loss of accuracy on ramps
# (median time-to-limit error 1.0% -> 0.3%). Chosen on synthetic data, not evaluation data.
CHANGEPOINT_PRIOR_SCALE = 0.01


@dataclass
class ForecastResult:
    status: str                              # crossing_predicted | no_crossing | insufficient_data | no_limit
    method: str
    reason: Optional[str] = None
    n_samples: int = 0
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None    # time of the last sample used ("now" for the forecast)
    last_bytes: Optional[int] = None
    limit_bytes: Optional[int] = None
    horizon_s: float = DEFAULT_HORIZON_S
    eta_s: Optional[float] = None            # point forecast reaches the limit
    eta_earliest_s: Optional[float] = None   # upper interval bound reaches the limit
    eta_latest_s: Optional[float] = None     # lower interval bound reaches the limit
    trend_bytes_per_min: Optional[float] = None
    # The forecast curve itself (point estimate), for evaluation: offsets from window_end, values in MiB
    future_offsets_s: list[float] = field(default_factory=list, repr=False)
    future_point_mib: list[float] = field(default_factory=list, repr=False)

    def predicted_mib_at(self, offset_s: float) -> Optional[float]:
        """Point forecast (MiB) at offset_s seconds after window_end, interpolated; None if outside the curve."""
        if not self.future_offsets_s or not (self.future_offsets_s[0] <= offset_s <= self.future_offsets_s[-1]):
            return None
        return float(np.interp(offset_s, self.future_offsets_s, self.future_point_mib))

    @property
    def current_percent(self) -> Optional[float]:
        if self.last_bytes is None or not self.limit_bytes:
            return None
        return 100.0 * self.last_bytes / self.limit_bytes

    def summary(self) -> str:
        def fmt(s):
            return "-" if s is None else f"{s / 60:.1f} min"
        pct = self.current_percent
        head = f"[{self.method}] {self.status}"
        if self.status == "insufficient_data" or pct is None:
            return f"{head} ({self.reason})"
        return (f"{head}: now {self.last_bytes / MiB:.1f}/{self.limit_bytes / MiB:.0f} MiB ({pct:.1f}%), "
                f"time-to-limit {fmt(self.eta_s)} (interval {fmt(self.eta_earliest_s)} .. {fmt(self.eta_latest_s)}), "
                f"trend {0 if self.trend_bytes_per_min is None else self.trend_bytes_per_min / MiB:+.2f} MiB/min, "
                f"n={self.n_samples}")


# ------------------------------------------------------------------
# Pure functions (unit-tested without a database)
# ------------------------------------------------------------------
def check_quality(times: Sequence[datetime]) -> Optional[str]:
    """Return a reason string if the series is unfit for forecasting, else None."""
    if len(times) < MIN_SAMPLES:
        return f"only {len(times)} samples (need {MIN_SAMPLES})"
    span = (times[-1] - times[0]).total_seconds()
    if span < MIN_SPAN_S:
        return f"span {span:.0f}s (need {MIN_SPAN_S:.0f}s)"
    gaps = np.diff([t.timestamp() for t in times])
    if gaps.size and gaps.max() > MAX_GAP_S:
        return f"gap of {gaps.max():.0f}s in samples (max {MAX_GAP_S:.0f}s)"
    return None


def first_crossing(offsets_s: np.ndarray, values: np.ndarray, limit: float) -> Optional[float]:
    """First future offset (seconds) where values reach the limit, linearly interpolated."""
    above = np.nonzero(values >= limit)[0]
    if above.size == 0:
        return None
    i = int(above[0])
    if i == 0:
        return float(offsets_s[0])
    x0, x1, y0, y1 = offsets_s[i - 1], offsets_s[i], values[i - 1], values[i]
    return float(x0 + (limit - y0) * (x1 - x0) / (y1 - y0)) if y1 != y0 else float(x1)


def forecast_series(
    times: Sequence[datetime],
    values_bytes: Sequence[int],
    limit_bytes: Optional[int],
    method: str = "prophet",
    horizon_s: float = DEFAULT_HORIZON_S,
    step_s: float = DEFAULT_STEP_S,
) -> ForecastResult:
    """Forecast one container's memory and estimate when it reaches its limit.

    times must be sorted, timezone-aware, and all from one container "life"
    (no restart in between). method: "prophet" or "linear" (least-squares line,
    the simple baseline the evaluation compares against).
    """
    result = ForecastResult(status="insufficient_data", method=method, n_samples=len(times),
                            limit_bytes=limit_bytes, horizon_s=horizon_s)
    if times:
        result.window_start, result.window_end = times[0], times[-1]
        result.last_bytes = int(values_bytes[-1])
    if not limit_bytes:
        result.status, result.reason = "no_limit", "container has no memory limit"
        return result
    problem = check_quality(times)
    if problem:
        result.reason = problem
        return result

    t0 = times[-1]
    x_hist = np.array([(t - t0).total_seconds() for t in times])         # <= 0
    y_hist = np.asarray(values_bytes, dtype=float) / MiB
    x_future = np.arange(step_s, horizon_s + step_s / 2, step_s)
    limit_mib = limit_bytes / MiB

    if method == "linear":
        slope, intercept = np.polyfit(x_hist, y_hist, 1)
        point = intercept + slope * x_future
        resid = y_hist - (intercept + slope * x_hist)
        # Residual band at the same nominal width as Prophet's interval (z = 1.2816 for 80%)
        band = 1.2816 * float(np.std(resid, ddof=2)) if len(resid) > 2 else 0.0
        upper, lower = point + band, point - band
        result.trend_bytes_per_min = slope * 60 * MiB
    elif method == "prophet":
        import pandas as pd
        from prophet import Prophet
        logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
        logging.getLogger("prophet").setLevel(logging.WARNING)
        naive = [t.astimezone(timezone.utc).replace(tzinfo=None) for t in times]
        model = Prophet(daily_seasonality=False, weekly_seasonality=False, yearly_seasonality=False,
                        interval_width=INTERVAL_WIDTH, changepoint_prior_scale=CHANGEPOINT_PRIOR_SCALE)
        model.fit(pd.DataFrame({"ds": naive, "y": y_hist}))
        future = pd.DataFrame({"ds": [naive[-1] + timedelta(seconds=float(s)) for s in x_future]})
        fc = model.predict(future)
        point, upper, lower = (fc[c].to_numpy() for c in ("yhat", "yhat_upper", "yhat_lower"))
        result.trend_bytes_per_min = float(point[-1] - point[0]) / ((x_future[-1] - x_future[0]) / 60) * MiB \
            if len(point) > 1 else None
    else:
        raise ValueError(f"unknown method {method!r}")

    result.future_offsets_s, result.future_point_mib = x_future.tolist(), np.asarray(point, dtype=float).tolist()
    result.eta_s = first_crossing(x_future, point, limit_mib)
    result.eta_earliest_s = first_crossing(x_future, upper, limit_mib)
    result.eta_latest_s = first_crossing(x_future, lower, limit_mib)
    result.status = "crossing_predicted" if result.eta_earliest_s is not None else "no_crossing"
    return result


def samples_since_restart(samples: Sequence[tuple], last_termination: Optional[datetime]) -> list[tuple]:
    """Keep only samples (time, ...) taken after the container's most recent termination."""
    if last_termination is None:
        return list(samples)
    return [s for s in samples if s[0] > last_termination]


# ------------------------------------------------------------------
# Database access
# ------------------------------------------------------------------
@dataclass
class ContainerSeries:
    pod: str
    container: str
    times: list[datetime] = field(default_factory=list)
    values: list[int] = field(default_factory=list)
    limit_bytes: Optional[int] = None
    last_termination: Optional[datetime] = None


def load_series(namespace: str, deployment: str, at: Optional[datetime] = None,
                lookback_s: float = DEFAULT_LOOKBACK_S, session_factory: Optional[Callable] = None
                ) -> list[ContainerSeries]:
    """Load each container's samples in (at - lookback, at], since its last termination before `at`."""
    from sqlalchemy import func

    from app.models.database import ContainerTermination, MemorySample, SessionLocal
    session_factory = session_factory or SessionLocal
    at = at or datetime.now(timezone.utc)
    start = at - timedelta(seconds=lookback_s)
    db = session_factory()
    try:
        rows = (db.query(MemorySample.pod, MemorySample.container, MemorySample.metric_timestamp,
                         MemorySample.memory_bytes, MemorySample.memory_limit_bytes)
                .filter(MemorySample.namespace == namespace, MemorySample.deployment == deployment,
                        MemorySample.metric_timestamp > start, MemorySample.metric_timestamp <= at)
                .order_by(MemorySample.pod, MemorySample.container, MemorySample.metric_timestamp).all())
        terms = dict(((p, c), ts) for p, c, ts in
                     db.query(ContainerTermination.pod, ContainerTermination.container,
                              func.max(ContainerTermination.finished_at))
                     .filter(ContainerTermination.namespace == namespace,
                             ContainerTermination.deployment == deployment,
                             ContainerTermination.finished_at <= at)
                     .group_by(ContainerTermination.pod, ContainerTermination.container).all())
    finally:
        db.close()

    series: dict[tuple, ContainerSeries] = {}
    for pod, container, ts, value, limit in rows:
        s = series.setdefault((pod, container), ContainerSeries(pod, container,
                                                                last_termination=terms.get((pod, container))))
        if s.last_termination is not None and ts <= s.last_termination:
            continue
        s.times.append(ts)
        s.values.append(int(value))
        s.limit_bytes = limit
    return [s for s in series.values() if s.times]


def forecast_deployment(namespace: str, deployment: str, method: str = "prophet",
                        at: Optional[datetime] = None, horizon_s: float = DEFAULT_HORIZON_S,
                        session_factory: Optional[Callable] = None) -> list[tuple[ContainerSeries, ForecastResult]]:
    out = []
    for s in load_series(namespace, deployment, at=at, session_factory=session_factory):
        out.append((s, forecast_series(s.times, s.values, s.limit_bytes, method=method, horizon_s=horizon_s)))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Forecast when a deployment's containers reach their memory limit.")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--method", choices=["prophet", "linear", "both"], default="both")
    parser.add_argument("--horizon-min", type=float, default=DEFAULT_HORIZON_S / 60)
    args = parser.parse_args(argv)
    methods = ["linear", "prophet"] if args.method == "both" else [args.method]
    for method in methods:
        results = forecast_deployment(args.namespace, args.deployment, method=method, horizon_s=args.horizon_min * 60)
        if not results:
            print(f"[{method}] no samples for {args.namespace}/{args.deployment} in the lookback window")
        for s, r in results:
            print(f"{s.pod}/{s.container}: {r.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

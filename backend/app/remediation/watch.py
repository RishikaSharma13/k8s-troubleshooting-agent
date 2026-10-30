"""Prediction watcher: turns memory forecasts into PREDICTED incidents (nothing is executed here).

For each deployment in the watched namespaces, the current life of each container is
forecast (linear method, the evaluated one). If the limit is expected within the horizon,
one incident is raised for that life, with a recommended action and the forecast's own
numbers as the reasoning. The database refuses a second incident for the same life
(unique index uq_incidents_forecast_life), so repeated scans never duplicate.

A container life starts at its last recorded termination. Kubernetes occasionally keeps no
termination record (observed for <1% of restarts), so a sudden drop of more than
DROP_SPLIT_BYTES also starts a new life, the same rule the evaluation uses.

Usage:
    PYTHONPATH=backend python -m app.remediation.watch --namespace remediate-lab [--once] [--interval 60]
"""
from __future__ import annotations

import argparse
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from loguru import logger
from sqlalchemy.dialects.postgresql import insert

from app.models.database import Incident, MemorySample, SessionLocal
from app.predictor.memory_forecast import DEFAULT_HORIZON_S, MiB, forecast_series, load_series

DROP_SPLIT_BYTES = 100 * MiB
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)   # "no termination recorded yet": the pod's first life


@dataclass
class Prediction:
    namespace: str
    deployment: str
    pod: str
    container: str
    life_started_at: datetime
    predicted_failure_at: datetime
    minutes_left: float
    last_mib: float
    limit_mib: float
    trend_mib_per_min: float


def current_life(times: list, values: list, last_termination: Optional[datetime]) -> tuple[list, list, datetime]:
    """The readings of the current life, and when it started (see module docstring)."""
    start = last_termination or EPOCH
    cut = 0
    for i in range(1, len(values)):
        if values[i - 1] - values[i] > DROP_SPLIT_BYTES:      # unrecorded restart: memory fell back to near zero
            cut, start = i, times[i]
    return times[cut:], values[cut:], start


def recommend(trend_mib_per_min: float) -> tuple[str, str]:
    """A steady climb is a leak: a planned restart is the real fix; more memory only buys time."""
    if trend_mib_per_min > 0:
        return ("RESTART_DEPLOYMENT",
                "Memory grows steadily (a leak): a planned restart now avoids an uncontrolled crash later. "
                "Raising the memory limit (INCREASE_MEMORY_LIMIT) would only delay it.")
    return ("INVESTIGATE_LOGS", "No steady growth: investigate before acting.")


def risk_level(minutes_left: float) -> str:
    return "HIGH" if minutes_left <= 10 else ("MEDIUM" if minutes_left <= 20 else "LOW")


def scan(namespace: str, now: Optional[datetime] = None, horizon_s: float = DEFAULT_HORIZON_S,
         session_factory: Callable = SessionLocal) -> list[Prediction]:
    """Forecast every deployment in the namespace; return the containers predicted to fail within the horizon."""
    now = now or datetime.now(timezone.utc)
    db = session_factory()
    try:
        deployments = [d for (d,) in db.query(MemorySample.deployment).filter(
            MemorySample.namespace == namespace, MemorySample.deployment.isnot(None),
            MemorySample.metric_timestamp > now - timedelta(minutes=15)).distinct().all()]
    finally:
        db.close()
    out = []
    for dep in sorted(deployments):
        for s in load_series(namespace, dep, at=now, session_factory=session_factory):
            times, values, life_start = current_life(s.times, s.values, s.last_termination)
            r = forecast_series(times, values, s.limit_bytes, method="linear", horizon_s=horizon_s)
            if r.status != "crossing_predicted" or r.eta_s is None:
                continue
            out.append(Prediction(namespace, dep, s.pod, s.container, life_start,
                                  r.window_end + timedelta(seconds=r.eta_s), r.eta_s / 60,
                                  values[-1] / MiB, s.limit_bytes / MiB, (r.trend_bytes_per_min or 0) / MiB))
    return out


def raise_incidents(predictions: list[Prediction], session_factory: Callable = SessionLocal) -> int:
    """Store one PENDING forecast incident per container life; returns how many were new."""
    if not predictions:
        return 0
    rows = []
    for p in predictions:
        action, why = recommend(p.trend_mib_per_min)
        rows.append(dict(
            id=str(uuid.uuid4()), pod_name=p.pod, deployment=p.deployment, namespace=p.namespace,
            risk_score=round(max(0.0, min(100.0, 100 * (1 - p.minutes_left / 30))), 1),
            risk_level=risk_level(p.minutes_left), status="PENDING", recommended_action=action,
            confidence=None,          # deliberately empty: no invented confidence number
            reasoning=(f"Forecast (linear): {p.last_mib:.0f}/{p.limit_mib:.0f} MiB, {p.trend_mib_per_min:+.1f} MiB/min, "
                       f"limit expected at {p.predicted_failure_at:%H:%M:%S} UTC (in {p.minutes_left:.1f} min). " + why)[:500],
            source="forecast", predicted_failure_at=p.predicted_failure_at, life_started_at=p.life_started_at))
    stmt = (insert(Incident).values(rows)
            .on_conflict_do_nothing(index_elements=["namespace", "deployment", "pod_name", "life_started_at"],
                                    index_where=Incident.source == "forecast")
            .returning(Incident.id, Incident.deployment, Incident.predicted_failure_at))
    db = session_factory()
    try:
        new = db.execute(stmt).fetchall()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    for _, dep, at in new:
        logger.info("PREDICTED incident raised: {} expected to reach its memory limit at {:%H:%M:%S} UTC", dep, at)
    return len(new)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Raise PREDICTED incidents from memory forecasts (executes nothing).")
    ap.add_argument("--namespace", action="append", required=True)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    while True:
        for ns in args.namespace:
            try:
                preds = scan(ns)
                new = raise_incidents(preds)
                logger.info("Scan {}: {} predicted within 30 min, {} new incident(s)", ns, len(preds), new)
            except Exception as exc:          # one bad scan must not stop the watcher
                logger.exception("Scan of {} failed: {}", ns, exc)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())

"""Verification: did the predicted crash happen, and did an approved action prevent it?

Once an incident's predicted failure time has passed (plus WINDOW_AFTER_S for forecast error
and for the collector to record the crash), it gets one outcome:

  acted on (a SUCCESS execution):  AVOIDED      no crash in the deployment between the action and
                                                the end of the window, and it is still running
                                   NOT_AVOIDED  a crash anyway
  not acted on:                    CONFIRMED    the crash happened (prediction error recorded)
                                   NOT_CONFIRMED no crash
  marked executed, no record:      UNVERIFIABLE (e.g. an action taken before executions were recorded)
  no readings around the predicted
  time and no crash:               UNVERIFIABLE (the collector was not running: no claim either way)

Checks are per deployment, not per pod name (a restart creates pods with new names), and use only
recorded data (terminations, memory readings, executions), so the answer is the same whenever it
runs. A memory drop of more than 100 MiB within one pod counts as a crash: Kubernetes occasionally
keeps no termination record (<1% of restarts in this lab).
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from loguru import logger

from app.models.database import ContainerTermination, Execution, Incident, MemorySample, SessionLocal
from app.remediation.watch import DROP_SPLIT_BYTES

WINDOW_AFTER_S = 300          # crashes up to 5 min after the predicted time still count as "the" crash
COLLECTOR_LAG_S = 60          # wait this much longer, so the crash is surely recorded


def _utc(t: Optional[datetime]) -> Optional[datetime]:
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=timezone.utc))


def first_crash(db, namespace: str, deployment: str, start: datetime, end: datetime,
                pod: Optional[str] = None) -> Optional[tuple[datetime, str]]:
    """Earliest crash of the deployment (or one pod) in [start, end]: (time, how it was detected)."""
    q = db.query(ContainerTermination.finished_at).filter(
        ContainerTermination.namespace == namespace, ContainerTermination.deployment == deployment,
        ContainerTermination.reason == "OOMKilled",
        ContainerTermination.finished_at > start, ContainerTermination.finished_at <= end)
    if pod:
        q = q.filter(ContainerTermination.pod == pod)
    recorded = q.order_by(ContainerTermination.finished_at).first()
    found = [(recorded[0], "OOMKilled")] if recorded else []
    s = db.query(MemorySample.pod, MemorySample.metric_timestamp, MemorySample.memory_bytes).filter(
        MemorySample.namespace == namespace, MemorySample.deployment == deployment,
        MemorySample.metric_timestamp > start, MemorySample.metric_timestamp <= end)
    if pod:
        s = s.filter(MemorySample.pod == pod)
    last: dict[str, int] = {}
    for p, t, b in s.order_by(MemorySample.pod, MemorySample.metric_timestamp).all():
        if p in last and last[p] - b > DROP_SPLIT_BYTES:
            found.append((t, "memory drop (restart without a termination record)"))
            break
        last[p] = b
    return min(found) if found else None


def still_running(db, namespace: str, deployment: str, start: datetime, end: datetime) -> bool:
    return db.query(MemorySample.id).filter(
        MemorySample.namespace == namespace, MemorySample.deployment == deployment,
        MemorySample.metric_timestamp > start, MemorySample.metric_timestamp <= end).first() is not None


def decide(db, inc: Incident) -> tuple[str, str, dict]:
    """(outcome, new status, details) for one due incident."""
    predicted = _utc(inc.predicted_failure_at)
    end = predicted + timedelta(seconds=WINDOW_AFTER_S)
    ex = (db.query(Execution).filter(Execution.incident_id == inc.id, Execution.status == "SUCCESS")
          .order_by(Execution.executed_at.desc()).first())
    details: dict = {"predicted_failure_at": predicted.isoformat(), "window_end": end.isoformat()}
    if ex is not None:
        acted_at = _utc(ex.executed_at)
        details.update(execution_id=ex.id, acted_at=acted_at.isoformat(),
                       minutes_ahead=round((predicted - acted_at).total_seconds() / 60, 1))
        crash = first_crash(db, inc.namespace, inc.deployment, acted_at, end)
        alive = still_running(db, inc.namespace, inc.deployment, predicted, end)
        details["still_running_after_predicted_time"] = alive
        if crash:
            details.update(crash_at=crash[0].isoformat(), detected_by=crash[1])
            return "NOT_AVOIDED", "FAILED", details
        if not alive:
            details["note"] = "no memory readings after the predicted time: the collector was not running"
            return "UNVERIFIABLE", inc.status, details
        return "AVOIDED", "RESOLVED", details
    if inc.status in ("EXECUTING", "RESOLVED"):
        details["note"] = "marked as executed, but no execution was recorded"
        return "UNVERIFIABLE", inc.status, details
    created = _utc(inc.created_at)
    crash = first_crash(db, inc.namespace, inc.deployment, created, end, pod=inc.pod_name)
    new_status = "EXPIRED" if inc.status in ("PENDING", "APPROVED") else inc.status
    if crash:
        details.update(crash_at=crash[0].isoformat(), detected_by=crash[1],
                       prediction_error_s=round((crash[0] - predicted).total_seconds()),
                       minutes_ahead=round((predicted - created).total_seconds() / 60, 1))
        return "CONFIRMED", new_status, details
    if not still_running(db, inc.namespace, inc.deployment, predicted - timedelta(minutes=2), end):
        details["note"] = "no memory readings around the predicted time: the collector was not running"
        return "UNVERIFIABLE", new_status, details
    return "NOT_CONFIRMED", new_status, details


def verify_due(now: Optional[datetime] = None, session_factory: Callable = SessionLocal,
               incident_id: Optional[str] = None) -> list[tuple[str, str]]:
    """Label every forecast incident whose window has closed (or just one). Returns (incident id, outcome)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=WINDOW_AFTER_S + COLLECTOR_LAG_S)
    db = session_factory()
    done = []
    try:
        q = db.query(Incident).filter(Incident.source == "forecast", Incident.outcome.is_(None),
                                      Incident.predicted_failure_at <= cutoff)
        if incident_id:
            q = q.filter(Incident.id == incident_id)
        for inc in q.order_by(Incident.predicted_failure_at).all():
            outcome, status, details = decide(db, inc)
            inc.outcome, inc.status, inc.outcome_detail = outcome, status, json.dumps(details)
            if status in ("RESOLVED", "FAILED", "EXPIRED"):
                inc.resolved_at = now.replace(tzinfo=None)
            if "execution_id" in details:
                ex = db.get(Execution, details["execution_id"])
                ex.verified_at = now.replace(tzinfo=None)
                evidence = json.loads(ex.evidence or "{}")
                evidence["verification"] = {"outcome": outcome, **details}
                ex.evidence = json.dumps(evidence)
            done.append((inc.id, outcome))
            logger.info("Verified incident {} ({}): {}", inc.id, inc.deployment, outcome)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return done


def report(session_factory: Callable = SessionLocal) -> str:
    """Counts per outcome, and the live prediction error of the forecasts that were not acted on."""
    db = session_factory()
    try:
        rows = db.query(Incident.namespace, Incident.outcome, Incident.outcome_detail).filter(
            Incident.source == "forecast", Incident.outcome.isnot(None)).all()
    finally:
        db.close()
    counts: dict[str, int] = {}
    errors, ahead = [], []
    for _, outcome, detail in rows:
        counts[outcome] = counts.get(outcome, 0) + 1
        d = json.loads(detail or "{}")
        if outcome == "CONFIRMED" and "prediction_error_s" in d:
            errors.append(abs(d["prediction_error_s"]))
        if outcome in ("AVOIDED", "NOT_AVOIDED") and "minutes_ahead" in d:
            ahead.append(d["minutes_ahead"])
    lines = ["Outcome         Incidents"] + [f"{k:<15} {v}" for k, v in sorted(counts.items())]
    if errors:
        lines.append(f"Not acted on, crash came true: median error {statistics.median(errors):.0f} s "
                     f"(max {max(errors)} s) over {len(errors)} incidents")
    if ahead:
        lines.append(f"Acted on: action taken a median {statistics.median(ahead):.1f} min before the predicted crash")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify forecast incidents whose predicted time has passed.")
    ap.add_argument("--report", action="store_true", help="only print the outcome summary")
    args = ap.parse_args(argv)
    if not args.report:
        print(f"verified {len(verify_due())} incident(s)")
    print(report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Verification: did the predicted crash happen, and did an approved action prevent it?

Once an incident's predicted failure time has passed (plus WINDOW_AFTER_S for forecast error
and for the collector to record the crash), it gets one outcome:

  acted on (a SUCCESS execution):  AVOIDED      the predicted container life did not crash after the
                                                action, and the workload is still running
                                   NOT_AVOIDED  the predicted container life crashed anyway
                                   (A crash of a *new* container life, e.g. a leak starting again after
                                   a restart, is a separate event with its own incident; it is recorded
                                   as next_crash_after_predicted_min, not counted against the action.)
  not acted on:                    CONFIRMED    the crash happened (prediction error recorded)
                                   NOT_CONFIRMED no crash
  marked executed, no record:      UNVERIFIABLE (e.g. an action taken before executions were recorded)
  no crash, but readings stopped or
  paused before the window closed: UNVERIFIABLE (collector stopped or laptop asleep: no claim either way)
  crashed after a pause:           CONFIRMED, but the error is kept out of the statistics (the pause shifted it)
  the predicted life ended for
  another reason first:            UNVERIFIABLE (e.g. a runtime restart, termination reason Unknown)

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


EXPLAIN_WINDOW_S = 180     # a recorded termination this soon before a memory drop explains the drop


def life_events(db, namespace: str, deployment: str, start: datetime, end: datetime,
                pod: Optional[str] = None, exclude_pod: Optional[str] = None) -> list[tuple[datetime, str, bool]]:
    """Every way a container life ended in [start, end], earliest first: (time, how, is_memory_crash).

    Recorded terminations of any reason are included; only OOMKilled is a memory crash. A memory drop
    of more than DROP_SPLIT_BYTES is a memory crash only when no termination was recorded for that pod
    in the EXPLAIN_WINDOW_S before it (Kubernetes occasionally keeps no record). A drop that a recorded
    non-memory termination explains (e.g. reason Unknown after a runtime restart) is not a memory crash.
    """
    q = db.query(ContainerTermination.pod, ContainerTermination.finished_at, ContainerTermination.reason).filter(
        ContainerTermination.namespace == namespace, ContainerTermination.deployment == deployment,
        ContainerTermination.finished_at > start, ContainerTermination.finished_at <= end)
    if pod:
        q = q.filter(ContainerTermination.pod == pod)
    if exclude_pod:
        q = q.filter(ContainerTermination.pod != exclude_pod)
    recorded = q.all()
    events = [(t, r or "unknown", r == "OOMKilled") for _, t, r in recorded]
    s = db.query(MemorySample.pod, MemorySample.metric_timestamp, MemorySample.memory_bytes).filter(
        MemorySample.namespace == namespace, MemorySample.deployment == deployment,
        MemorySample.metric_timestamp > start, MemorySample.metric_timestamp <= end)
    if pod:
        s = s.filter(MemorySample.pod == pod)
    if exclude_pod:
        s = s.filter(MemorySample.pod != exclude_pod)
    last: dict[str, int] = {}
    for p, t, b in s.order_by(MemorySample.pod, MemorySample.metric_timestamp).all():
        if p in last and last[p] - b > DROP_SPLIT_BYTES:
            explained = any(rp == p and t - timedelta(seconds=EXPLAIN_WINDOW_S) <= rt <= t for rp, rt, _ in recorded)
            if not explained:
                events.append((t, "memory drop (restart without a termination record)", True))
        last[p] = b
    return sorted(events)


def first_crash(db, namespace: str, deployment: str, start: datetime, end: datetime,
                pod: Optional[str] = None, exclude_pod: Optional[str] = None) -> Optional[tuple[datetime, str]]:
    """Earliest memory crash of the deployment (or one pod) in [start, end]: (time, how it was detected)."""
    for t, how, is_crash in life_events(db, namespace, deployment, start, end, pod, exclude_pod):
        if is_crash:
            return t, how
    return None


def still_running(db, namespace: str, deployment: str, start: datetime, end: datetime) -> bool:
    return db.query(MemorySample.id).filter(
        MemorySample.namespace == namespace, MemorySample.deployment == deployment,
        MemorySample.metric_timestamp > start, MemorySample.metric_timestamp <= end).first() is not None


PAUSE_S = 90              # a gap in readings longer than this means the collector or the laptop paused


def reading_times(db, namespace: str, deployment: str, start: datetime, end: datetime, pod: Optional[str] = None) -> list:
    q = db.query(MemorySample.metric_timestamp).filter(
        MemorySample.namespace == namespace, MemorySample.deployment == deployment,
        MemorySample.metric_timestamp > start, MemorySample.metric_timestamp <= end)
    if pod:
        q = q.filter(MemorySample.pod == pod)
    return [t for (t,) in q.order_by(MemorySample.metric_timestamp).all()]


def longest_gap_s(times: list) -> float:
    return max(((b - a).total_seconds() for a, b in zip(times, times[1:])), default=0.0)


def observed_through(times: list, start: datetime, end: datetime) -> bool:
    """Readings cover [start, end] without a pause: needed before claiming that nothing happened."""
    return bool(times) and (times[0] - start).total_seconds() <= PAUSE_S and \
        (end - times[-1]).total_seconds() <= PAUSE_S and longest_gap_s(times) <= PAUSE_S


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
        # the predicted container life: did it crash after the action?
        crash = first_crash(db, inc.namespace, inc.deployment, acted_at, end, pod=inc.pod_name)
        alive = still_running(db, inc.namespace, inc.deployment, predicted, end)
        details["still_running_after_predicted_time"] = alive
        # a new container life crashing later is a separate event: record it, do not count it
        nxt = first_crash(db, inc.namespace, inc.deployment, acted_at, end, exclude_pod=inc.pod_name)
        if nxt:
            details.update(next_crash_at=nxt[0].isoformat(),
                           next_crash_after_predicted_min=round((nxt[0] - predicted).total_seconds() / 60, 1))
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
    new_status = "EXPIRED" if inc.status in ("PENDING", "APPROVED") else inc.status
    events = life_events(db, inc.namespace, inc.deployment, created, end, pod=inc.pod_name)
    if events and not events[0][2]:
        t, how, _ = events[0]
        details.update(ended_at=t.isoformat(), ended_by=how,
                       note=f"the predicted container life ended for another reason ({how}) before any memory crash")
        return "UNVERIFIABLE", new_status, details
    crash = (events[0][0], events[0][1]) if events else None
    if crash:
        error = round((crash[0] - predicted).total_seconds())
        details.update(crash_at=crash[0].isoformat(), detected_by=crash[1],
                       minutes_ahead=round((predicted - created).total_seconds() / 60, 1))
        gap = longest_gap_s(reading_times(db, inc.namespace, inc.deployment, created, crash[0], pod=inc.pod_name))
        if gap > PAUSE_S:
            # it did crash, but a pause (laptop asleep, collector stopped) shifted the timing: keep it out of error stats
            details.update(prediction_error_paused_s=error, timing_affected_by_pause_s=round(gap))
        else:
            details["prediction_error_s"] = error
        return "CONFIRMED", new_status, details
    window_start = predicted - timedelta(minutes=2)
    if not observed_through(reading_times(db, inc.namespace, inc.deployment, window_start, end, pod=inc.pod_name),
                            window_start, end):
        details["note"] = ("readings stopped or paused before the window closed (collector not running or laptop "
                           "asleep): no claim either way")
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
    errors, ahead, nxt = [], [], []
    for _, outcome, detail in rows:
        counts[outcome] = counts.get(outcome, 0) + 1
        d = json.loads(detail or "{}")
        if outcome == "CONFIRMED" and "prediction_error_s" in d:
            errors.append(abs(d["prediction_error_s"]))
        if outcome in ("AVOIDED", "NOT_AVOIDED") and "minutes_ahead" in d:
            ahead.append(d["minutes_ahead"])
        if outcome == "AVOIDED" and "next_crash_after_predicted_min" in d:
            nxt.append(d["next_crash_after_predicted_min"])
    paused = sum(1 for _, o, d in rows if o == "CONFIRMED" and "timing_affected_by_pause_s" in json.loads(d or "{}"))
    lines = ["Outcome         Incidents"] + [f"{k:<15} {v}" for k, v in sorted(counts.items())]
    if errors:
        lines.append(f"Not acted on, crash came true: median error {statistics.median(errors):.0f} s "
                     f"(max {max(errors)} s) over {len(errors)} incidents"
                     + (f"; {paused} more came true after a pause, timing not counted" if paused else ""))
    elif paused:
        lines.append(f"Not acted on: {paused} more came true after a pause, timing not counted")
    if ahead:
        lines.append(f"Acted on: action taken a median {statistics.median(ahead):.1f} min before the predicted crash")
    if nxt:
        lines.append(f"Avoided, but the workload crashed again within the window in {len(nxt)} case(s), a median "
                     f"{statistics.median(nxt):+.1f} min after the predicted time (new container life: a restart "
                     "delays a leak, it does not fix it)")
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

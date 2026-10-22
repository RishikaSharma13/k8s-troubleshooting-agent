"""Collector for the additional predictions: storage eviction and Job deadlines.

Signals (stored in signal_samples):
- ephemeral_storage_bytes: a pod's local storage use (kubelet stats summary),
  limit = the sum of its containers' ephemeral-storage limits.
- job_succeeded: a Job's completed items, limit = spec.completions,
  deadline_at = status.startTime + spec.activeDeadlineSeconds.

Outcomes (stored in outcome_events): Evicted pods, and Jobs that ended
DeadlineExceeded (failure) or Complete (success, the negative case).

Storage rule, from the Session 15 pilot: the kubelet re-stamps its disk figure
every few seconds but only re-measures it every ~70-100 s. So a value is stored
when it changes (timestamped when first seen), plus a re-confirmation every
HEARTBEAT_S so that a steady value still produces readings. Stale repeats of an
unchanged value are not stored as if they were new measurements.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy.dialects.postgresql import insert

from app.collector.memory_collector import parse_quantity_bytes, parse_timestamp, resolve_deployment
from app.kubernetes.kubectl_executor import KubectlExecutor
from app.models.database import OutcomeEvent, SessionLocal, SignalSample

HEARTBEAT_S = 180.0
STORAGE_SIGNAL = "ephemeral_storage_bytes"
JOB_SIGNAL = "job_succeeded"


@dataclass(frozen=True)
class SignalRecord:
    namespace: str
    workload_kind: str
    workload: str
    object_name: str
    signal: str
    observed_at: datetime
    value: float
    limit_value: Optional[float]
    deadline_at: Optional[datetime] = None


@dataclass(frozen=True)
class OutcomeRecord:
    namespace: str
    workload_kind: str
    workload: str
    object_name: str
    event_type: str
    occurred_at: datetime
    message: Optional[str] = None


@dataclass
class SignalResult:
    namespace: str
    readings_seen: int = 0
    readings_stored: int = 0
    outcomes_new: int = 0
    errors: list[str] = field(default_factory=list)


# ------------------------------------------------------------------
# Pure functions
# ------------------------------------------------------------------
def pod_storage_limit(pod: dict[str, Any]) -> Optional[int]:
    """Sum of the containers' ephemeral-storage limits (what the kubelet enforces); None if any is unset."""
    total = 0
    for c in pod.get("spec", {}).get("containers", []) or []:
        raw = (c.get("resources", {}).get("limits") or {}).get("ephemeral-storage")
        if not raw:
            return None
        total += parse_quantity_bytes(raw)
    return total or None


def owner_of_job(job: dict[str, Any]) -> tuple[str, str]:
    """(kind, name) of the workload behind a Job: its CronJob if it has one, else the Job itself."""
    for owner in job.get("metadata", {}).get("ownerReferences", []) or []:
        if owner.get("kind") == "CronJob":
            return "CronJob", owner.get("name", "")
    return "Job", job.get("metadata", {}).get("name", "")


def build_storage_readings(namespace: str, pods: dict[str, Any], summaries: list[dict[str, Any]],
                           now: datetime) -> list[SignalRecord]:
    """Ephemeral-storage use for each Running pod that has a storage limit."""
    by_name = {p["metadata"]["name"]: p for p in pods.get("items", []) or []
               if p.get("status", {}).get("phase") == "Running"}
    out = []
    for summary in summaries:
        for ps in summary.get("pods", []) or []:
            ref = ps.get("podRef", {})
            pod = by_name.get(ref.get("name"))
            if ref.get("namespace") != namespace or pod is None:
                continue
            used = (ps.get("ephemeral-storage") or {}).get("usedBytes")
            limit = pod_storage_limit(pod)
            if used is None or limit is None:
                continue
            dep = resolve_deployment(pod)
            out.append(SignalRecord(namespace, "Deployment" if dep else "Pod", dep or ref["name"], ref["name"],
                                    STORAGE_SIGNAL, now, float(used), float(limit)))
    return out


def build_job_readings(namespace: str, jobs: dict[str, Any], now: datetime) -> list[SignalRecord]:
    """Progress of each running Job that has completions and a deadline."""
    out = []
    for job in jobs.get("items", []) or []:
        spec, st = job.get("spec", {}), job.get("status", {})
        if any(c.get("status") == "True" and c.get("type") in ("Complete", "Failed")
               for c in st.get("conditions", []) or []):
            continue  # finished: its outcome is recorded instead
        if not st.get("startTime") or not spec.get("completions") or not spec.get("activeDeadlineSeconds"):
            continue
        kind, workload = owner_of_job(job)
        deadline = parse_timestamp(st["startTime"]) + timedelta(seconds=spec["activeDeadlineSeconds"])
        out.append(SignalRecord(namespace, kind, workload, job["metadata"]["name"], JOB_SIGNAL, now,
                                float(st.get("succeeded", 0) or 0), float(spec["completions"]), deadline))
    return out


def build_outcomes(namespace: str, pods: dict[str, Any], jobs: dict[str, Any], now: datetime) -> list[OutcomeRecord]:
    out = []
    for pod in pods.get("items", []) or []:
        st = pod.get("status", {})
        if st.get("phase") == "Failed" and st.get("reason") == "Evicted":
            finished = None
            for cs in st.get("containerStatuses", []) or []:
                t = (cs.get("state") or {}).get("terminated") or {}
                if t.get("finishedAt"):
                    finished = parse_timestamp(t["finishedAt"])
            dep = resolve_deployment(pod)
            out.append(OutcomeRecord(namespace, "Deployment" if dep else "Pod", dep or pod["metadata"]["name"],
                                     pod["metadata"]["name"], "Evicted", finished or now, (st.get("message") or "")[:1000]))
    for job in jobs.get("items", []) or []:
        kind, workload = owner_of_job(job)
        for c in job.get("status", {}).get("conditions", []) or []:
            if c.get("status") != "True" or not c.get("lastTransitionTime"):
                continue
            if c.get("type") == "Failed" and c.get("reason") == "DeadlineExceeded":
                event = "DeadlineExceeded"
            elif c.get("type") == "Complete":
                event = "Complete"
            else:
                continue
            out.append(OutcomeRecord(namespace, kind, workload, job["metadata"]["name"], event,
                                     parse_timestamp(c["lastTransitionTime"]), (c.get("message") or "")[:1000]))
    return out


class ChangeFilter:
    """Keeps a reading if its value changed, or HEARTBEAT_S passed since the last kept one (per series)."""

    def __init__(self, heartbeat_s: float = HEARTBEAT_S):
        self.heartbeat_s = heartbeat_s
        self.last: dict[tuple, tuple[float, datetime]] = {}

    def keep(self, records: list[SignalRecord]) -> list[SignalRecord]:
        kept = []
        for r in records:
            key = (r.namespace, r.object_name, r.signal)
            prev = self.last.get(key)
            if prev is None or r.value != prev[0] or (r.observed_at - prev[1]).total_seconds() >= self.heartbeat_s:
                kept.append(r)
                self.last[key] = (r.value, r.observed_at)
        return kept


# ------------------------------------------------------------------
# Storage
# ------------------------------------------------------------------
def _insert(model, constraint: str, rows: list, session_factory: Callable) -> int:
    if not rows:
        return 0
    stmt = (insert(model).values([asdict(r) for r in rows])
            .on_conflict_do_nothing(constraint=constraint).returning(model.id))
    db = session_factory()
    try:
        n = len(db.execute(stmt).fetchall())
        db.commit()
        return n
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def store_signals(rows: list[SignalRecord], session_factory: Callable = SessionLocal) -> int:
    return _insert(SignalSample, "uq_signal_samples_reading", rows, session_factory)


def store_outcomes(rows: list[OutcomeRecord], session_factory: Callable = SessionLocal) -> int:
    return _insert(OutcomeEvent, "uq_outcome_events_event", rows, session_factory)


# ------------------------------------------------------------------
# Collector
# ------------------------------------------------------------------
class SignalCollector:
    def __init__(self, executor: Optional[KubectlExecutor] = None, session_factory: Callable = SessionLocal,
                 heartbeat_s: float = HEARTBEAT_S):
        self.executor = executor or KubectlExecutor()
        self.session_factory = session_factory
        self.filter = ChangeFilter(heartbeat_s)

    def _json(self, *args) -> Optional[dict]:
        r = self.executor.run(*args)
        return r.json() if r.success else None

    def collect_once(self, namespace: str, now: Optional[datetime] = None) -> SignalResult:
        """One cycle. Each part is independent: a failure in one is reported, the others still run."""
        now = now or datetime.now(timezone.utc).replace(microsecond=0)
        result = SignalResult(namespace)
        pods = self._json("get", "pods", "-n", namespace, "-o", "json")
        jobs = self._json("get", "jobs", "-n", namespace, "-o", "json")
        if pods is None:
            result.errors.append("pod list unavailable")
        if jobs is None:
            result.errors.append("job list unavailable")

        readings: list[SignalRecord] = []
        if pods is not None:
            nodes = sorted({p.get("spec", {}).get("nodeName") for p in pods.get("items", []) or []} - {None})
            summaries = [s for s in (self._json("get", "--raw", f"/api/v1/nodes/{n}/proxy/stats/summary")
                                     for n in nodes) if s is not None]
            if len(summaries) < len(nodes):
                result.errors.append("kubelet stats unavailable for some nodes")
            readings += build_storage_readings(namespace, pods, summaries, now)
        if jobs is not None:
            readings += build_job_readings(namespace, jobs, now)
        result.readings_seen = len(readings)

        try:
            result.readings_stored = store_signals(self.filter.keep(readings), self.session_factory)
            result.outcomes_new = store_outcomes(build_outcomes(namespace, pods or {}, jobs or {}, now),
                                                 self.session_factory)
        except Exception as exc:
            result.errors.append(f"store failed: {exc}")
            logger.error("Storing signals failed for {}: {}", namespace, exc)
            return result
        if result.readings_seen or result.outcomes_new or result.errors:
            logger.info("Signals {}: seen={} stored={} outcomes_new={} errors={}", namespace,
                        result.readings_seen, result.readings_stored, result.outcomes_new, result.errors)
        return result

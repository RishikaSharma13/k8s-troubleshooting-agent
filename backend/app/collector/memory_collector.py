"""Memory sample collector.

Reads container memory from the Kubernetes Metrics API and stores it in
memory_samples. Rules, each validated by the Session 1 measurement pilot:

1. The time of a sample is the Metrics API timestamp, not the poll time
   (readings were 10-25 s older than the poll that returned them).
2. A reading is stored once: (namespace, pod, container, metric_timestamp)
   is unique, and duplicates are ignored (polls often re-return a reading).
3. A pod without metrics is skipped, never estimated (this happens for
   ~15 s after each container restart).
4. Memory is stored in bytes, with the container's memory limit.

Each cycle also records container terminations (e.g. OOMKilled) from the pod
list it already fetched: the ground truth that forecasts are scored against.
Terminations are recorded even when the Metrics API is unavailable.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy.dialects.postgresql import insert

from app.kubernetes.kubectl_executor import KubectlExecutor
from app.models.database import ContainerTermination, MemorySample, SessionLocal

_BINARY_SUFFIXES = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}
_DECIMAL_SUFFIXES = {"k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "P": 10**15, "E": 10**18}
_DURATION_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "ns": 1e-9}
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(h|ms|us|µs|ns|m|s)")


@dataclass(frozen=True)
class MemorySampleRecord:
    namespace: str
    pod: str
    container: str
    deployment: Optional[str]
    metric_timestamp: datetime
    window_seconds: Optional[float]
    memory_bytes: int
    memory_limit_bytes: Optional[int]


@dataclass(frozen=True)
class TerminationRecord:
    namespace: str
    pod: str
    container: str
    deployment: Optional[str]
    reason: Optional[str]
    exit_code: Optional[int]
    started_at: Optional[datetime]
    finished_at: datetime
    restart_count: Optional[int]


@dataclass
class CollectionResult:
    namespace: str
    samples_found: int = 0
    inserted: int = 0
    duplicates: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (pod, reason)
    error: Optional[str] = None
    terminations_found: int = 0
    terminations_inserted: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


# ------------------------------------------------------------------
# Parsing helpers (pure functions, unit-tested without a cluster)
# ------------------------------------------------------------------
def parse_quantity_bytes(quantity: str) -> int:
    """Convert a Kubernetes memory quantity ("432Ki", "260Mi", "1G", "1e3") to bytes."""
    q = quantity.strip()
    try:
        for suffix, factor in _BINARY_SUFFIXES.items():
            if q.endswith(suffix):
                return int(Decimal(q[: -len(suffix)]) * factor)
        if q and q[-1] in _DECIMAL_SUFFIXES:
            return int(Decimal(q[:-1]) * _DECIMAL_SUFFIXES[q[-1]])
        if q.endswith("m"):  # milli-units: valid Kubernetes syntax, rare for memory
            return int(Decimal(q[:-1]) / 1000)
        return int(Decimal(q))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"unrecognised quantity: {quantity!r}") from exc


def parse_duration_seconds(duration: Optional[str]) -> Optional[float]:
    """Convert a Go duration ("14.81s", "424ms", "1m0.5s") to seconds; None if absent or invalid."""
    if not duration:
        return None
    parts = _DURATION_PART.findall(duration)
    if not parts or "".join(n + u for n, u in parts) != duration:
        return None
    return sum(float(n) * _DURATION_UNITS[u] for n, u in parts)


def parse_timestamp(value: str) -> datetime:
    """Parse an RFC 3339 timestamp ("2026-10-15T10:21:35Z") into an aware UTC datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def resolve_deployment(pod: dict[str, Any]) -> Optional[str]:
    """Return the owning Deployment's name, or None.

    A Deployment-managed pod is owned by a ReplicaSet named
    "<deployment>-<pod-template-hash>". Pods not owned that way (bare pods,
    StatefulSets, DaemonSets, Jobs) return None rather than a guess.
    """
    metadata = pod.get("metadata", {})
    template_hash = metadata.get("labels", {}).get("pod-template-hash")
    for owner in metadata.get("ownerReferences", []) or []:
        if owner.get("kind") == "ReplicaSet" and template_hash:
            name = owner.get("name", "")
            suffix = f"-{template_hash}"
            if name.endswith(suffix) and len(name) > len(suffix):
                return name[: -len(suffix)]
    return None


def container_memory_limits(pod: dict[str, Any]) -> dict[str, Optional[int]]:
    """Map container name -> memory limit in bytes (None when no limit is set)."""
    limits: dict[str, Optional[int]] = {}
    for c in pod.get("spec", {}).get("containers", []) or []:
        raw = (c.get("resources", {}).get("limits") or {}).get("memory")
        limits[c.get("name", "")] = parse_quantity_bytes(raw) if raw else None
    return limits


def build_samples(
    namespace: str,
    pod_metrics: dict[str, Any],
    pods: dict[str, Any],
) -> tuple[list[MemorySampleRecord], list[tuple[str, str]]]:
    """Join Metrics API readings with pod specs.

    Returns (samples, skipped). Pods without metrics, and metrics for pods
    no longer listed, are skipped with a reason; nothing is estimated.
    """
    pods_by_name = {p.get("metadata", {}).get("name"): p for p in pods.get("items", []) or []}
    samples: list[MemorySampleRecord] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()

    for item in pod_metrics.get("items", []) or []:
        name = item.get("metadata", {}).get("name", "")
        seen.add(name)
        pod = pods_by_name.get(name)
        if pod is None:
            skipped.append((name, "pod_not_listed"))
            continue
        try:
            ts = parse_timestamp(item["timestamp"])
        except (KeyError, ValueError):
            skipped.append((name, "bad_timestamp"))
            continue
        window = parse_duration_seconds(item.get("window"))
        deployment = resolve_deployment(pod)
        limits = container_memory_limits(pod)
        for c in item.get("containers", []) or []:
            raw = (c.get("usage") or {}).get("memory")
            if raw is None:
                skipped.append((f"{name}/{c.get('name')}", "no_memory_value"))
                continue
            try:
                memory_bytes = parse_quantity_bytes(raw)
            except ValueError:
                skipped.append((f"{name}/{c.get('name')}", "bad_memory_value"))
                continue
            samples.append(MemorySampleRecord(
                namespace=namespace, pod=name, container=c.get("name", ""),
                deployment=deployment, metric_timestamp=ts, window_seconds=window,
                memory_bytes=memory_bytes, memory_limit_bytes=limits.get(c.get("name", "")),
            ))

    for name in pods_by_name:
        if name not in seen:
            skipped.append((name, "no_metrics"))
    return samples, skipped


def build_terminations(namespace: str, pods: dict[str, Any]) -> list[TerminationRecord]:
    """Extract container terminations from a pod list.

    Uses state.terminated (container currently stopped) and lastState.terminated
    (the previous run). Kubernetes keeps only the most recent termination per
    container, so restart_count is recorded to detect missed events later.
    """
    records: dict[tuple, TerminationRecord] = {}
    for pod in pods.get("items", []) or []:
        name = pod.get("metadata", {}).get("name", "")
        deployment = resolve_deployment(pod)
        for cs in pod.get("status", {}).get("containerStatuses", []) or []:
            for source in ((cs.get("state") or {}).get("terminated"),
                           (cs.get("lastState") or {}).get("terminated")):
                if not source or not source.get("finishedAt"):
                    continue
                try:
                    finished = parse_timestamp(source["finishedAt"])
                    started = parse_timestamp(source["startedAt"]) if source.get("startedAt") else None
                except ValueError:
                    continue
                key = (name, cs.get("name", ""), finished)
                records[key] = TerminationRecord(
                    namespace=namespace, pod=name, container=cs.get("name", ""),
                    deployment=deployment, reason=source.get("reason"),
                    exit_code=source.get("exitCode"), started_at=started,
                    finished_at=finished, restart_count=cs.get("restartCount"),
                )
    return list(records.values())


# ------------------------------------------------------------------
# Storage
# ------------------------------------------------------------------
def store_samples(samples: list[MemorySampleRecord], session_factory: Callable = SessionLocal) -> int:
    """Insert samples, ignoring readings already stored. Returns the number inserted."""
    if not samples:
        return 0
    rows = [asdict(s) for s in samples]
    stmt = (insert(MemorySample).values(rows)
            .on_conflict_do_nothing(constraint="uq_memory_samples_reading")
            .returning(MemorySample.id))
    db = session_factory()
    try:
        inserted = len(db.execute(stmt).fetchall())
        db.commit()
        return inserted
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def insert_terminations(records: list[TerminationRecord], session_factory: Callable = SessionLocal) -> list:
    """Insert terminations, ignoring events already stored. Returns only the newly inserted rows."""
    if not records:
        return []
    stmt = (insert(ContainerTermination).values([asdict(r) for r in records])
            .on_conflict_do_nothing(constraint="uq_container_terminations_event")
            .returning(ContainerTermination.pod, ContainerTermination.container,
                       ContainerTermination.reason, ContainerTermination.finished_at))
    db = session_factory()
    try:
        new_rows = db.execute(stmt).fetchall()
        db.commit()
        return new_rows
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def store_terminations(records: list[TerminationRecord], session_factory: Callable = SessionLocal) -> int:
    """Insert terminations, ignoring events already stored. Returns the number inserted."""
    return len(insert_terminations(records, session_factory))


# ------------------------------------------------------------------
# Collector
# ------------------------------------------------------------------
class MemoryCollector:
    def __init__(self, executor: Optional[KubectlExecutor] = None,
                 session_factory: Callable = SessionLocal) -> None:
        self.executor = executor or KubectlExecutor()
        self.session_factory = session_factory

    def fetch(self, namespace: str) -> tuple[Optional[dict], Optional[dict], Optional[str]]:
        """Return (pod_metrics, pods, error). Read-only kubectl calls."""
        metrics = self.executor.run("get", "--raw", f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods")
        if not metrics.success or metrics.json() is None:
            return None, None, f"metrics API unavailable: {metrics.error or 'invalid JSON'} {metrics.stderr.strip()}".strip()
        pods = self.executor.run("get", "pods", "-n", namespace, "-o", "json")
        if not pods.success or pods.json() is None:
            return None, None, f"pod list unavailable: {pods.error or 'invalid JSON'} {pods.stderr.strip()}".strip()
        return metrics.json(), pods.json(), None

    def fetch_pods(self, namespace: str) -> tuple[Optional[dict], Optional[str]]:
        """Return (pods, error). Read-only kubectl call."""
        pods = self.executor.run("get", "pods", "-n", namespace, "-o", "json")
        if not pods.success or pods.json() is None:
            return None, f"pod list unavailable: {pods.error or 'invalid JSON'} {pods.stderr.strip()}".strip()
        return pods.json(), None

    def fetch_metrics(self, namespace: str) -> tuple[Optional[dict], Optional[str]]:
        """Return (pod_metrics, error). Read-only kubectl call."""
        metrics = self.executor.run("get", "--raw", f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods")
        if not metrics.success or metrics.json() is None:
            return None, f"metrics API unavailable: {metrics.error or 'invalid JSON'} {metrics.stderr.strip()}".strip()
        return metrics.json(), None

    def collect_once(self, namespace: str) -> CollectionResult:
        """One collection cycle. On any fetch error, stores nothing estimated (no fallback values)."""
        result = CollectionResult(namespace=namespace)
        pods, error = self.fetch_pods(namespace)
        if error:
            result.error = error
            logger.warning("Collection skipped for {}: {}", namespace, error)
            return result

        # Ground truth first: terminations come from the pod list, independent of the Metrics API.
        terminations = build_terminations(namespace, pods)
        result.terminations_found = len(terminations)
        try:
            new_terminations = insert_terminations(terminations, self.session_factory)
        except Exception as exc:
            result.error = f"store terminations failed: {exc}"
            logger.error("Storing terminations failed for {}: {}", namespace, exc)
            return result
        result.terminations_inserted = len(new_terminations)
        for pod, container, reason, finished_at in new_terminations:  # newly recorded events only
            logger.info("Termination recorded: {}/{} container={} reason={} finished_at={}",
                        namespace, pod, container, reason, finished_at.isoformat())

        pod_metrics, error = self.fetch_metrics(namespace)
        if error:
            result.error = error
            logger.warning("Memory samples skipped for {}: {}", namespace, error)
            return result
        samples, result.skipped = build_samples(namespace, pod_metrics, pods)
        result.samples_found = len(samples)
        try:
            result.inserted = store_samples(samples, self.session_factory)
        except Exception as exc:  # database problems are reported, not hidden
            result.error = f"store failed: {exc}"
            logger.error("Storing samples failed for {}: {}", namespace, exc)
            return result
        result.duplicates = result.samples_found - result.inserted
        logger.info("Collected {}: found={} inserted={} duplicates={} skipped={} terminations_new={}",
                    namespace, result.samples_found, result.inserted, result.duplicates, result.skipped,
                    result.terminations_inserted)
        return result

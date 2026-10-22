"""Tests for storage and Job-deadline signal collection.

Fixtures are shaped like the Session 15 pilot: a pod with a 100Mi
ephemeral-storage limit that was Evicted, and Jobs owned by a CronJob.
"""
import json
import unittest
from datetime import datetime, timedelta, timezone

import pytest

from app.collector import run
from app.collector.signals import (
    JOB_SIGNAL, STORAGE_SIGNAL, ChangeFilter, SignalCollector, SignalRecord, build_job_readings,
    build_outcomes, build_storage_readings, owner_of_job, pod_storage_limit, store_outcomes, store_signals,
)
from app.kubernetes.kubectl_executor import KubectlResult

MiB = 1024 * 1024
NOW = datetime(2026, 10, 22, 7, 0, 0, tzinfo=timezone.utc)
NS = "pytest-signals"


def pod(name="disk-leak-6f7d9c8b5-x2k4p", phase="Running", limit="100Mi", reason=None, finished=None, ns=NS):
    status = {"phase": phase}
    if reason:
        status.update(reason=reason, message="Pod ephemeral local storage usage exceeds the total limit of containers 100Mi.")
    if finished:
        status["containerStatuses"] = [{"name": "writer", "state": {"terminated": {"exitCode": 137, "finishedAt": finished}}}]
    resources = {"limits": {"ephemeral-storage": limit}} if limit else {}
    return {"metadata": {"name": name, "namespace": ns, "labels": {"pod-template-hash": "6f7d9c8b5"},
                         "ownerReferences": [{"kind": "ReplicaSet", "name": "disk-leak-6f7d9c8b5"}]},
            "spec": {"nodeName": "aiops-lab-control-plane", "containers": [{"name": "writer", "resources": resources}]},
            "status": status}


def summary(name="disk-leak-6f7d9c8b5-x2k4p", used=40 * MiB, ns=NS):
    return {"pods": [{"podRef": {"name": name, "namespace": ns},
                      "ephemeral-storage": {"usedBytes": used, "time": "2026-10-22T06:59:58Z"}}]}


def job(name="deadline-job-29842710", succeeded=7, conditions=None, owner=True):
    meta = {"name": name}
    if owner:
        meta["ownerReferences"] = [{"kind": "CronJob", "name": "deadline-job"}]
    return {"metadata": meta, "spec": {"completions": 20, "activeDeadlineSeconds": 285},
            "status": {"startTime": "2026-10-22T06:58:00Z", "succeeded": succeeded,
                       **({"conditions": conditions} if conditions else {})}}


class TestStorage(unittest.TestCase):
    def test_limit_is_sum_of_containers(self):
        p = pod()
        p["spec"]["containers"].append({"name": "sidecar", "resources": {"limits": {"ephemeral-storage": "50Mi"}}})
        self.assertEqual(pod_storage_limit(p), 150 * MiB)
        self.assertIsNone(pod_storage_limit(pod(limit=None)))

    def test_reading_for_running_pod_with_limit(self):
        [r] = build_storage_readings(NS, {"items": [pod()]}, [summary()], NOW)
        self.assertEqual((r.signal, r.workload_kind, r.workload, r.value, r.limit_value),
                         (STORAGE_SIGNAL, "Deployment", "disk-leak", 40 * MiB, 100 * MiB))
        self.assertEqual(r.observed_at, NOW)          # first-seen time, not the kubelet's re-stamped time

    def test_skips_finished_unlimited_and_foreign_pods(self):
        items = [pod(phase="Failed"), pod(name="no-limit", limit=None)]
        sums = [summary(), summary(name="no-limit"), summary(name="other", ns="elsewhere")]
        self.assertEqual(build_storage_readings(NS, {"items": items}, sums, NOW), [])


class TestJobs(unittest.TestCase):
    def test_running_job_reading_with_deadline(self):
        [r] = build_job_readings(NS, {"items": [job()]}, NOW)
        self.assertEqual((r.signal, r.workload_kind, r.workload, r.value, r.limit_value),
                         (JOB_SIGNAL, "CronJob", "deadline-job", 7.0, 20.0))
        self.assertEqual(r.deadline_at, datetime(2026, 10, 22, 7, 2, 45, tzinfo=timezone.utc))

    def test_finished_or_unbounded_jobs_are_skipped(self):
        done = job(conditions=[{"type": "Complete", "status": "True", "lastTransitionTime": "2026-10-22T07:01:00Z"}])
        unbounded = job(name="no-deadline"); del unbounded["spec"]["activeDeadlineSeconds"]
        self.assertEqual(build_job_readings(NS, {"items": [done, unbounded]}, NOW), [])

    def test_owner(self):
        self.assertEqual(owner_of_job(job()), ("CronJob", "deadline-job"))
        self.assertEqual(owner_of_job(job(name="adhoc", owner=False)), ("Job", "adhoc"))


class TestOutcomes(unittest.TestCase):
    def test_eviction_deadline_and_completion(self):
        evicted = pod(phase="Failed", reason="Evicted", finished="2026-10-22T06:26:42Z")
        late = job(name="deadline-job-1", conditions=[{"type": "Failed", "status": "True", "reason": "DeadlineExceeded",
                                                       "message": "Job was active longer than specified deadline",
                                                       "lastTransitionTime": "2026-10-22T07:02:45Z"}])
        ok = job(name="deadline-job-2", conditions=[{"type": "Complete", "status": "True",
                                                     "lastTransitionTime": "2026-10-22T07:01:30Z"}])
        out = {(o.object_name, o.event_type): o for o in build_outcomes(NS, {"items": [evicted, pod()]},
                                                                       {"items": [late, ok, job()]}, NOW)}
        self.assertEqual(set(out), {("disk-leak-6f7d9c8b5-x2k4p", "Evicted"), ("deadline-job-1", "DeadlineExceeded"),
                                    ("deadline-job-2", "Complete")})
        ev = out[("disk-leak-6f7d9c8b5-x2k4p", "Evicted")]
        self.assertEqual((ev.workload, ev.occurred_at), ("disk-leak", datetime(2026, 10, 22, 6, 26, 42, tzinfo=timezone.utc)))
        self.assertIn("ephemeral local storage", ev.message)


class TestChangeFilter(unittest.TestCase):
    def _r(self, seconds, value):
        return SignalRecord(NS, "Deployment", "d", "p", STORAGE_SIGNAL, NOW + timedelta(seconds=seconds), value, 100.0)

    def test_stale_repeats_dropped_changes_and_heartbeats_kept(self):
        f = ChangeFilter(heartbeat_s=180)
        kept = [f.keep([self._r(t, v)]) for t, v in [(0, 12), (15, 12), (30, 12), (75, 40), (90, 40), (255, 40)]]
        self.assertEqual([len(k) for k in kept], [1, 0, 0, 1, 0, 1])   # first, change at 75 s, heartbeat at 255 s


class FakeExecutor:
    def __init__(self, pods, jobs, summaries_ok=True):
        self.pods, self.jobs, self.summaries_ok = pods, jobs, summaries_ok

    def run(self, *args, timeout=30):
        if args[1] == "--raw":
            if not self.summaries_ok:
                return KubectlResult(list(args), False, stderr="forbidden", error="non-zero exit")
            return KubectlResult(list(args), True, stdout=json.dumps(summary()))
        payload = self.pods if args[1] == "pods" else self.jobs
        return KubectlResult(list(args), True, stdout=json.dumps(payload))


@pytest.mark.db
class TestStorageAndCollector(unittest.TestCase):
    def setUp(self):
        from app.models.database import OutcomeEvent, SessionLocal, SignalSample
        self.S, self.O, self.Session = SignalSample, OutcomeEvent, SessionLocal
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.S).filter(self.S.namespace == NS).delete()
            db.query(self.O).filter(self.O.namespace == NS).delete()
            db.commit()
        finally:
            db.close()

    def _count(self, model):
        db = self.Session()
        try:
            return db.query(model).filter(model.namespace == NS).count()
        finally:
            db.close()

    def test_storage_is_idempotent(self):
        rows = build_storage_readings(NS, {"items": [pod()]}, [summary()], NOW)
        self.assertEqual((store_signals(rows), store_signals(rows)), (1, 0))
        evicted = build_outcomes(NS, {"items": [pod(phase="Failed", reason="Evicted", finished="2026-10-22T06:26:42Z")]},
                                 {"items": []}, NOW)
        self.assertEqual((store_outcomes(evicted), store_outcomes(evicted)), (1, 0))

    def test_collect_cycles_store_changes_only(self):
        c = SignalCollector(FakeExecutor({"items": [pod()]}, {"items": [job()]}))
        first = c.collect_once(NS, now=NOW)
        second = c.collect_once(NS, now=NOW + timedelta(seconds=15))      # same values: nothing new stored
        self.assertEqual((first.readings_seen, first.readings_stored, second.readings_stored), (2, 2, 0))
        self.assertEqual(self._count(self.S), 2)

    def test_stats_failure_does_not_block_job_progress(self):
        c = SignalCollector(FakeExecutor({"items": [pod()]}, {"items": [job()]}, summaries_ok=False))
        r = c.collect_once(NS, now=NOW)
        self.assertEqual(r.readings_stored, 1)                             # the Job reading still arrived
        self.assertIn("kubelet stats unavailable for some nodes", r.errors)


def test_signal_crash_never_stops_memory_collection(monkeypatch):
    calls = []

    class Mem:
        def collect_once(self, ns):
            calls.append(("memory", ns))

    class Broken:
        def collect_once(self, ns):
            raise RuntimeError("bug in signal code")

    monkeypatch.setattr(run, "MemoryCollector", Mem)
    monkeypatch.setattr(run, "SignalCollector", Broken)
    assert run.main(["--namespace", "a", "--namespace", "b", "--cycles", "2", "--interval", "1"]) == 0
    assert calls == [("memory", "a"), ("memory", "b"), ("memory", "a"), ("memory", "b")]

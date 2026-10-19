"""Tests for recording container terminations (OOMKill ground truth).

Pod status fixtures are shaped like real kubelet output: an OOMKilled
container shows reason "OOMKilled" and exit code 137 in lastState.terminated.
"""
import json
import unittest
from datetime import datetime, timezone

import pytest

from app.collector.memory_collector import MemoryCollector, build_terminations, store_terminations
from app.kubernetes.kubectl_executor import KubectlResult

NS = "pytest-terminations"


def pod_with_status(name="leak-steady-7d9f8c6b5-abcde", template_hash="7d9f8c6b5", restarts=3,
                    last=None, current=None, container="memload"):
    status = {"name": container, "restartCount": restarts, "state": {}, "lastState": {}}
    if last:
        status["lastState"]["terminated"] = last
    if current:
        status["state"]["terminated"] = current
    else:
        status["state"]["running"] = {"startedAt": "2026-10-19T09:31:02Z"}
    return {"metadata": {"name": name, "labels": {"pod-template-hash": template_hash},
                         "ownerReferences": [{"kind": "ReplicaSet", "name": f"leak-steady-{template_hash}"}]},
            "spec": {"containers": [{"name": container}]},
            "status": {"containerStatuses": [status]}}


OOM = {"reason": "OOMKilled", "exitCode": 137,
       "startedAt": "2026-10-19T09:10:40Z", "finishedAt": "2026-10-19T09:31:00Z"}


class TestBuildTerminations(unittest.TestCase):
    def test_oomkill_in_last_state(self):
        [t] = build_terminations("predict-lab", {"items": [pod_with_status(last=OOM)]})
        self.assertEqual((t.reason, t.exit_code, t.restart_count), ("OOMKilled", 137, 3))
        self.assertEqual(t.deployment, "leak-steady")
        self.assertEqual(t.finished_at, datetime(2026, 10, 19, 9, 31, 0, tzinfo=timezone.utc))
        self.assertEqual(t.started_at, datetime(2026, 10, 19, 9, 10, 40, tzinfo=timezone.utc))

    def test_current_and_previous_termination_both_recorded(self):
        current = {"reason": "OOMKilled", "exitCode": 137,
                   "startedAt": "2026-10-19T09:31:10Z", "finishedAt": "2026-10-19T09:51:20Z"}
        records = build_terminations("predict-lab", {"items": [pod_with_status(last=OOM, current=current)]})
        self.assertEqual(sorted(r.finished_at.minute for r in records), [31, 51])

    def test_running_container_without_history_has_no_terminations(self):
        self.assertEqual(build_terminations("predict-lab", {"items": [pod_with_status(restarts=0)]}), [])

    def test_missing_finished_at_is_ignored(self):
        self.assertEqual(build_terminations("predict-lab", {"items": [pod_with_status(last={"reason": "Error"})]}), [])

    def test_pod_without_status_is_ignored(self):
        self.assertEqual(build_terminations("predict-lab", {"items": [{"metadata": {"name": "p"}}]}), [])


class _Executor:
    """Fake kubectl: pod list always works; the Metrics API can be made to fail."""
    def __init__(self, pods, metrics_ok=True):
        self.pods, self.metrics_ok = pods, metrics_ok

    def run(self, *args, timeout=30):
        if args[1] == "--raw":
            if not self.metrics_ok:
                return KubectlResult(list(args), False, stderr="Metrics API not available", error="non-zero exit")
            return KubectlResult(list(args), True, stdout=json.dumps({"items": []}))
        return KubectlResult(list(args), True, stdout=json.dumps(self.pods))


@pytest.mark.db
class TestTerminationStorage(unittest.TestCase):
    def setUp(self):
        from app.models.database import ContainerTermination, MemorySample, SessionLocal
        self.Term, self.Sample, self.Session = ContainerTermination, MemorySample, SessionLocal
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.Term).filter(self.Term.namespace == NS).delete()
            db.query(self.Sample).filter(self.Sample.namespace == NS).delete()
            db.commit()
        finally:
            db.close()

    def _count(self):
        db = self.Session()
        try:
            return db.query(self.Term).filter(self.Term.namespace == NS).count()
        finally:
            db.close()

    def test_same_termination_seen_every_cycle_is_stored_once(self):
        records = build_terminations(NS, {"items": [pod_with_status(last=OOM)]})
        self.assertEqual(store_terminations(records), 1)
        self.assertEqual(store_terminations(records), 0)
        self.assertEqual(self._count(), 1)

    def test_terminations_recorded_even_when_metrics_api_is_down(self):
        collector = MemoryCollector(_Executor({"items": [pod_with_status(last=OOM)]}, metrics_ok=False))
        result = collector.collect_once(NS)
        self.assertFalse(result.ok)                      # memory samples could not be taken...
        self.assertEqual(result.terminations_inserted, 1)  # ...but the OOMKill was still recorded
        self.assertEqual(self._count(), 1)

"""Tests for the memory sample collector.

Fixture data is shaped like real readings from the Session 1 measurement
pilot (kind cluster, metrics-server at 15 s resolution).
"""
import unittest
from datetime import datetime, timezone

import pytest

from app.collector.memory_collector import (
    MemoryCollector,
    MemorySampleRecord,
    build_samples,
    container_memory_limits,
    parse_duration_seconds,
    parse_quantity_bytes,
    parse_timestamp,
    resolve_deployment,
    store_samples,
)
from app.kubernetes.kubectl_executor import KubectlResult

NS = "pytest-memory-collector"  # isolates database rows written by these tests


def make_pod(name, template_hash=None, owner_kind="ReplicaSet", owner_name=None, limit="256Mi", container="hogger"):
    labels = {"app": name.rsplit("-", 2)[0]}
    if template_hash:
        labels["pod-template-hash"] = template_hash
    owners = [{"kind": owner_kind, "name": owner_name}] if owner_name else []
    resources = {"limits": {"memory": limit}} if limit else {}
    return {
        "metadata": {"name": name, "labels": labels, "ownerReferences": owners},
        "spec": {"containers": [{"name": container, "resources": resources}]},
    }


def make_metric(name, timestamp, window, memory, container="hogger"):
    return {"metadata": {"name": name}, "timestamp": timestamp, "window": window,
            "containers": [{"name": container, "usage": {"cpu": "500000000n", "memory": memory}}]}


PODS = {"items": [
    make_pod("cpu-hog-55755854d4-js4vx", "55755854d4", owner_name="cpu-hog-55755854d4"),
    make_pod("cpu-hog-55755854d4-nw5rq", "55755854d4", owner_name="cpu-hog-55755854d4"),
    make_pod("memory-test-app-6b6b6747c8-ht8lq", "6b6b6747c8", owner_name="memory-test-app-6b6b6747c8",
             limit="260Mi", container="stress"),
]}
# Pilot poll at 10:23:02: js4vx had just restarted and had no metrics yet.
METRICS = {"items": [
    make_metric("cpu-hog-55755854d4-nw5rq", "2026-10-15T10:22:44Z", "19.877s", "432Ki"),
    make_metric("memory-test-app-6b6b6747c8-ht8lq", "2026-10-15T10:22:52Z", "17.494s", "185588Ki",
                container="stress"),
]}


class TestParsing(unittest.TestCase):
    def test_quantities(self):
        cases = {"432Ki": 442368, "185588Ki": 190042112, "260Mi": 272629760, "1Gi": 2**30,
                 "446464": 446464, "129M": 129_000_000, "1k": 1000, "1e3": 1000, "1.5Gi": 1610612736}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_quantity_bytes(raw), expected)

    def test_bad_quantity_raises(self):
        with self.assertRaises(ValueError):
            parse_quantity_bytes("lots")

    def test_durations(self):
        self.assertAlmostEqual(parse_duration_seconds("14.814569792s"), 14.814569792)
        self.assertAlmostEqual(parse_duration_seconds("424ms"), 0.424)
        self.assertAlmostEqual(parse_duration_seconds("1m0.5s"), 60.5)
        self.assertIsNone(parse_duration_seconds(""))
        self.assertIsNone(parse_duration_seconds("soon"))

    def test_timestamp_is_utc_aware(self):
        ts = parse_timestamp("2026-10-15T10:21:35Z")
        self.assertEqual(ts, datetime(2026, 10, 15, 10, 21, 35, tzinfo=timezone.utc))


class TestDeploymentResolution(unittest.TestCase):
    def test_replicaset_owned_pod(self):
        pod = make_pod("cpu-hog-55755854d4-js4vx", "55755854d4", owner_name="cpu-hog-55755854d4")
        self.assertEqual(resolve_deployment(pod), "cpu-hog")

    def test_deployment_name_containing_dashes(self):
        pod = make_pod("memory-test-app-6b6b6747c8-ht8lq", "6b6b6747c8", owner_name="memory-test-app-6b6b6747c8")
        self.assertEqual(resolve_deployment(pod), "memory-test-app")

    def test_not_a_deployment(self):
        self.assertIsNone(resolve_deployment(make_pod("bare-pod")))
        self.assertIsNone(resolve_deployment(make_pod("db-0", owner_kind="StatefulSet", owner_name="db")))
        # ReplicaSet without the template-hash label: do not guess
        self.assertIsNone(resolve_deployment(make_pod("x-abc", owner_name="x-abc")))

    def test_limits(self):
        self.assertEqual(container_memory_limits(make_pod("p", limit="260Mi")), {"hogger": 272629760})
        self.assertEqual(container_memory_limits(make_pod("p", limit=None)), {"hogger": None})


class TestBuildSamples(unittest.TestCase):
    def test_joins_metrics_with_pods_and_skips_missing(self):
        samples, skipped = build_samples("default", METRICS, PODS)
        self.assertEqual(len(samples), 2)
        mem = next(s for s in samples if s.deployment == "memory-test-app")
        self.assertEqual(mem.container, "stress")
        self.assertEqual(mem.memory_bytes, 190042112)
        self.assertEqual(mem.memory_limit_bytes, 272629760)
        self.assertEqual(mem.metric_timestamp, datetime(2026, 10, 15, 10, 22, 52, tzinfo=timezone.utc))
        self.assertAlmostEqual(mem.window_seconds, 17.494)
        # The restarted pod is skipped, not estimated
        self.assertEqual(skipped, [("cpu-hog-55755854d4-js4vx", "no_metrics")])

    def test_metrics_for_unlisted_pod_are_skipped(self):
        metrics = {"items": [make_metric("gone-pod", "2026-10-15T10:22:44Z", "15s", "1Mi")]}
        samples, skipped = build_samples("default", metrics, {"items": []})
        self.assertEqual(samples, [])
        self.assertEqual(skipped, [("gone-pod", "pod_not_listed")])


class FakeExecutor:
    def __init__(self, metrics=METRICS, pods=PODS, fail=None):
        self.metrics, self.pods, self.fail = metrics, pods, fail
        self.calls = []

    def run(self, *args, timeout=30):
        import json
        self.calls.append(args)
        if self.fail == "metrics" and args[1] == "--raw":
            return KubectlResult(list(args), False, stderr="Metrics API not available",
                                 error="kubectl returned a non-zero exit code")
        payload = self.metrics if args[1] == "--raw" else self.pods
        return KubectlResult(list(args), True, stdout=json.dumps(payload))


class TestCollectorWithoutDatabase(unittest.TestCase):
    def test_metrics_api_failure_stores_nothing(self):
        def no_db():
            raise AssertionError("database must not be touched when the fetch fails")
        result = MemoryCollector(FakeExecutor(fail="metrics"), session_factory=no_db).collect_once("default")
        self.assertFalse(result.ok)
        self.assertIn("metrics API unavailable", result.error)
        self.assertEqual(result.inserted, 0)

    def test_fetch_uses_read_only_commands(self):
        executor = FakeExecutor()
        MemoryCollector(executor).fetch("default")
        self.assertEqual(executor.calls[0], ("get", "--raw", "/apis/metrics.k8s.io/v1beta1/namespaces/default/pods"))
        self.assertEqual(executor.calls[1], ("get", "pods", "-n", "default", "-o", "json"))


@pytest.mark.db
class TestStorage(unittest.TestCase):
    def setUp(self):
        from app.models.database import MemorySample, SessionLocal
        self.Model, self.Session = MemorySample, SessionLocal
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.Model).filter(self.Model.namespace == NS).delete()
            db.commit()
        finally:
            db.close()

    def _count(self):
        db = self.Session()
        try:
            return db.query(self.Model).filter(self.Model.namespace == NS).count()
        finally:
            db.close()

    def _sample(self, second, memory=1024):
        return MemorySampleRecord(NS, "pod-a", "main", "app", datetime(2026, 10, 15, 10, 0, second, tzinfo=timezone.utc),
                                  15.0, memory, 2048)

    def test_duplicate_readings_are_stored_once(self):
        self.assertEqual(store_samples([self._sample(0), self._sample(15)]), 2)
        self.assertEqual(store_samples([self._sample(0), self._sample(15)]), 0)  # same readings again
        self.assertEqual(store_samples([self._sample(15), self._sample(30)]), 1)  # one new reading
        self.assertEqual(self._count(), 3)

    def test_collect_once_end_to_end_with_fake_cluster(self):
        metrics = {"items": [make_metric("pod-a", "2026-10-15T10:22:44Z", "15s", "432Ki")]}
        pods = {"items": [make_pod("pod-a", "abc12", owner_name="app-abc12")]}
        collector = MemoryCollector(FakeExecutor(metrics=metrics, pods=pods))
        first = collector.collect_once(NS)
        second = collector.collect_once(NS)  # Metrics API returns the same reading again
        self.assertEqual((first.inserted, first.duplicates), (1, 0))
        self.assertEqual((second.inserted, second.duplicates), (0, 1))
        self.assertEqual(self._count(), 1)

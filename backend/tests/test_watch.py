"""Tests for the prediction watcher (PREDICTED incidents; nothing is executed)."""
import unittest
from datetime import datetime, timedelta, timezone

import pytest

from app.predictor.memory_forecast import MiB
from app.remediation.watch import EPOCH, current_life, raise_incidents, recommend, risk_level, scan

NS = "pytest-watch"
T0 = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)


class TestRules(unittest.TestCase):
    def test_life_starts_at_last_termination(self):
        times = [T0 + timedelta(seconds=15 * i) for i in range(4)]
        self.assertEqual(current_life(times, [10 * MiB] * 4, T0)[2], T0)
        self.assertEqual(current_life(times, [10 * MiB] * 4, None)[2], EPOCH)

    def test_unrecorded_restart_starts_a_new_life(self):
        times = [T0 + timedelta(seconds=15 * i) for i in range(5)]
        values = [240 * MiB, 250 * MiB, 5 * MiB, 9 * MiB, 13 * MiB]        # dropped without a recorded kill
        t, v, start = current_life(times, values, None)
        self.assertEqual((start, v), (times[2], values[2:]))

    def test_recommendation_and_risk(self):
        self.assertEqual(recommend(12.0)[0], "RESTART_DEPLOYMENT")
        self.assertIn("INCREASE_MEMORY_LIMIT", recommend(12.0)[1])            # alternative named, honestly
        self.assertEqual(recommend(0.0)[0], "INVESTIGATE_LOGS")
        self.assertEqual([risk_level(m) for m in (5, 15, 25)], ["HIGH", "MEDIUM", "LOW"])


@pytest.mark.db
class TestWatcher(unittest.TestCase):
    def setUp(self):
        from app.models.database import ContainerTermination, Incident, MemorySample, SessionLocal
        self.M, self.T, self.I, self.Session = MemorySample, ContainerTermination, Incident, SessionLocal
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            for model in (self.M, self.T, self.I):
                db.query(model).filter(model.namespace.in_([NS, NS + "-other"])).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def _ramp(self, start, mib0, n, ns=NS, dep="leak-x", step_mib=3.0):
        """Readings every 15 s, +3 MiB each (12 MiB/min), like leak-steady."""
        db = self.Session()
        try:
            for i in range(n):
                db.add(self.M(namespace=ns, pod=dep + "-p", container="memload", deployment=dep,
                              metric_timestamp=start + timedelta(seconds=15 * i), window_seconds=15,
                              memory_bytes=int((mib0 + step_mib * i) * MiB), memory_limit_bytes=256 * MiB))
            db.commit()
        finally:
            db.close()

    def _incidents(self):
        db = self.Session()
        try:
            return db.query(self.I).filter(self.I.namespace == NS).order_by(self.I.predicted_failure_at).all()
        finally:
            db.close()

    def test_leak_raises_one_incident_per_life(self):
        self._ramp(T0, 100, 20)                                   # 100 -> 157 MiB over ~5 min
        now = T0 + timedelta(seconds=15 * 19)
        preds = scan(NS, now=now)
        self.assertEqual(len(preds), 1)
        p = preds[0]
        # (256 - 157) / 12 MiB per min = 8.25 min after the last reading
        self.assertAlmostEqual((p.predicted_failure_at - now).total_seconds() / 60, 8.25, delta=0.3)
        self.assertEqual((raise_incidents(preds), raise_incidents(scan(NS, now=now))), (1, 0))   # never duplicated
        [inc] = self._incidents()
        self.assertEqual((inc.source, inc.status, inc.recommended_action, inc.risk_level),
                         ("forecast", "PENDING", "RESTART_DEPLOYMENT", "HIGH"))
        self.assertIsNone(inc.confidence)
        self.assertEqual(inc.life_started_at, EPOCH)
        self.assertIn("12.0 MiB/min", inc.reasoning)

    def test_new_life_after_termination_gets_its_own_incident(self):
        self._ramp(T0, 100, 20)
        raise_incidents(scan(NS, now=T0 + timedelta(seconds=285)))
        killed = T0 + timedelta(minutes=10)
        db = self.Session()
        try:
            db.add(self.T(namespace=NS, pod="leak-x-p", container="memload", deployment="leak-x", reason="OOMKilled",
                          exit_code=137, finished_at=killed))
            db.commit()
        finally:
            db.close()
        self._ramp(killed + timedelta(seconds=15), 100, 20)
        self.assertEqual(raise_incidents(scan(NS, now=killed + timedelta(seconds=300))), 1)
        lives = [i.life_started_at for i in self._incidents()]
        self.assertEqual(lives, [EPOCH, killed])

    def test_flat_or_short_series_raise_nothing(self):
        self._ramp(T0, 200, 20, step_mib=0.0)                     # flat
        self._ramp(T0, 100, 3, dep="new-pod")                     # too few readings
        self.assertEqual(scan(NS, now=T0 + timedelta(seconds=285)), [])

    def test_only_the_requested_namespace(self):
        self._ramp(T0, 100, 20, ns=NS + "-other")
        self.assertEqual(scan(NS, now=T0 + timedelta(seconds=285)), [])

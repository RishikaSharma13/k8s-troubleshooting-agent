"""Tests for verification: one scenario per outcome, from recorded data only."""
import json
import unittest
from datetime import datetime, timedelta, timezone

import pytest

from app.predictor.memory_forecast import MiB
from app.remediation.verify import report, verify_due

NS, DEP, POD = "pytest-verify", "leak-v", "leak-v-old"
P = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)                 # predicted crash time
LATER = P + timedelta(minutes=7)                                      # after the window has closed
naive = lambda t: t.replace(tzinfo=None)


@pytest.mark.db
class TestVerify(unittest.TestCase):
    def setUp(self):
        from app.models.database import ContainerTermination, Execution, Incident, MemorySample, SessionLocal
        self.T, self.E, self.I, self.M, self.Session = ContainerTermination, Execution, Incident, MemorySample, SessionLocal
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.E).filter(self.E.incident_id.like("pytest-v-%")).delete(synchronize_session=False)
            db.query(self.I).filter(self.I.id.like("pytest-v-%")).delete(synchronize_session=False)
            for m in (self.T, self.M):
                db.query(m).filter(m.namespace == NS).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def _add(self, *rows):
        db = self.Session()
        try:
            db.add_all(rows)
            db.commit()
        finally:
            db.close()

    def _incident(self, iid="pytest-v-1", status="PENDING", executed_at=None, dep=DEP):
        rows = [self.I(id=iid, pod_name=POD, deployment=dep, namespace=NS, risk_score=50, risk_level="HIGH",
                       status=status, recommended_action="RESTART_DEPLOYMENT", source="forecast",
                       created_at=naive(P - timedelta(minutes=18)), predicted_failure_at=P,
                       life_started_at=P - timedelta(minutes=20))]
        if executed_at:
            rows.append(self.E(id=iid + "-x", incident_id=iid, action="RESTART_DEPLOYMENT", status="SUCCESS",
                               executed_at=naive(executed_at), evidence=json.dumps({"command": "kubectl patch ..."})))
        self._add(*rows)

    def _readings(self, pod, start, end, mib=lambda i: 10 + 3 * i):
        n = int((end - start).total_seconds() // 15)
        self._add(*[self.M(namespace=NS, pod=pod, container="memload", deployment=DEP,
                           metric_timestamp=start + timedelta(seconds=15 * i), window_seconds=15,
                           memory_bytes=int(mib(i) * MiB), memory_limit_bytes=256 * MiB) for i in range(n)])

    def _oom(self, at, pod=POD, dep=DEP):
        self._add(self.T(namespace=NS, pod=pod, container="memload", deployment=dep, reason="OOMKilled",
                         exit_code=137, finished_at=at))

    def _get(self, iid="pytest-v-1"):
        db = self.Session()
        try:
            inc = db.get(self.I, iid)
            ex = db.get(self.E, iid + "-x")
            return inc, json.loads(inc.outcome_detail or "{}"), ex
        finally:
            db.close()

    # ---- acted on ----
    def test_avoided(self):
        acted = P - timedelta(minutes=15)
        self._incident(status="EXECUTING", executed_at=acted)
        self._readings("leak-v-new", acted + timedelta(seconds=20), LATER)        # new pod, running, no crash
        self.assertEqual(verify_due(now=LATER), [("pytest-v-1", "AVOIDED")])
        inc, d, ex = self._get()
        self.assertEqual((inc.status, d["minutes_ahead"]), ("RESOLVED", 15.0))
        self.assertIsNotNone(ex.verified_at)
        self.assertEqual(json.loads(ex.evidence)["verification"]["outcome"], "AVOIDED")

    def test_not_avoided_when_it_crashed_anyway(self):
        acted = P - timedelta(minutes=15)
        self._incident(status="EXECUTING", executed_at=acted)
        self._readings("leak-v-new", acted, LATER)
        self._oom(P - timedelta(minutes=2), pod="leak-v-new")                    # any pod of the deployment counts
        verify_due(now=LATER)
        inc, d, _ = self._get()
        self.assertEqual((inc.outcome, inc.status, d["detected_by"]), ("NOT_AVOIDED", "FAILED", "OOMKilled"))

    def test_unrecorded_restart_counts_as_a_crash(self):
        acted = P - timedelta(minutes=15)
        self._incident(status="EXECUTING", executed_at=acted)
        self._readings("leak-v-new", acted, LATER, mib=lambda i: 250 if i == 30 else (5 if i == 31 else 100))
        verify_due(now=LATER)
        inc, d, _ = self._get()
        self.assertEqual(inc.outcome, "NOT_AVOIDED")
        self.assertIn("memory drop", d["detected_by"])

    def test_no_readings_after_the_predicted_time_makes_no_claim(self):
        acted = P - timedelta(minutes=15)
        self._incident(status="EXECUTING", executed_at=acted)
        self._readings("leak-v-new", acted, P - timedelta(minutes=5))           # collector stopped (laptop off)
        verify_due(now=LATER)
        inc, d, _ = self._get()
        self.assertEqual(inc.outcome, "UNVERIFIABLE")                           # neither AVOIDED nor NOT_AVOIDED
        self.assertIn("collector was not running", d["note"])

    # ---- not acted on ----
    def test_confirmed_with_prediction_error(self):
        self._incident()
        self._oom(P + timedelta(seconds=30))
        verify_due(now=LATER)
        inc, d, _ = self._get()
        self.assertEqual((inc.outcome, inc.status, d["prediction_error_s"], d["minutes_ahead"]),
                         ("CONFIRMED", "EXPIRED", 30, 18.0))

    def test_not_confirmed(self):
        self._incident()
        self._readings(POD, P - timedelta(minutes=3), LATER, mib=lambda i: 200)     # running, no crash
        self._oom(P + timedelta(seconds=30), pod="other", dep="other-deployment")  # someone else's crash
        verify_due(now=LATER)
        self.assertEqual(self._get()[0].outcome, "NOT_CONFIRMED")

    def test_no_data_around_the_predicted_time_is_not_a_verdict(self):
        self._incident()                                                         # laptop was off: no readings
        verify_due(now=LATER)
        inc, d, _ = self._get()
        self.assertEqual((inc.outcome, inc.status), ("UNVERIFIABLE", "EXPIRED"))

    def test_executed_without_a_record_is_unverifiable(self):
        self._incident(status="EXECUTING")
        verify_due(now=LATER)
        inc, d, _ = self._get()
        self.assertEqual((inc.outcome, inc.status), ("UNVERIFIABLE", "EXECUTING"))

    # ---- timing ----
    def test_not_before_the_window_closes_and_only_once(self):
        self._incident()
        self._oom(P + timedelta(seconds=30))
        self.assertEqual(verify_due(now=P + timedelta(minutes=5)), [])           # too early
        self.assertIsNone(self._get()[0].outcome)
        self.assertEqual(len(verify_due(now=LATER)), 1)
        self.assertEqual(verify_due(now=LATER + timedelta(hours=1)), [])        # never re-verified

    def test_report(self):
        self._incident()
        self._oom(P + timedelta(seconds=30))
        verify_due(now=LATER)
        text = report()
        self.assertIn("CONFIRMED", text)
        self.assertIn("median error 30 s", text)

    def test_api_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self._incident(status="EXECUTING", executed_at=P - timedelta(minutes=15))
        self._readings("leak-v-new", P - timedelta(minutes=14), LATER)
        # The window of P (a fixed past date) has closed, so the endpoint verifies it straight away
        r = TestClient(app).post("/api/v1/remediation/verify", params={"incident_id": "pytest-v-1"}).json()
        self.assertEqual((r["status"], r["outcome"], r["new_incident_status"]), ("success", "AVOIDED", "RESOLVED"))

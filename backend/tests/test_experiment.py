"""Tests for the controlled experiment runner (a stand-in API records every call)."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.remediation import experiment as ex

NS = "pytest-exp"
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)


class FakeAPI:
    def __init__(self, approve_ok=True):
        self.calls, self.approve_ok = [], approve_ok

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, params["incident_id"], params["action"]))
        if endpoint.endswith("/approve"):
            return {"status": "success" if self.approve_ok else "error", "error": None if self.approve_ok else "nope"}
        return {"status": "success", "execution_details": {"status": "COMPLETED", "execution_id": "x-" + params["incident_id"]}}


class TestGuards(unittest.TestCase):
    def test_protected_namespace_and_bounds(self):
        log = Path(tempfile.mkdtemp()) / "e.jsonl"
        with self.assertRaises(ValueError):
            ex.Experiment("predict-lab", 5, log, FakeAPI())
        for bad in (0, ex.MAX_COUNT + 1):
            with self.assertRaises(ValueError):
                ex.Experiment(NS, bad, log, FakeAPI())

    def test_refuses_without_confirm(self):
        log = Path(tempfile.mkdtemp()) / "e.jsonl"
        self.assertEqual(ex.main(["run", "--count", "2", "--namespace", NS, "--log", str(log)]), 2)
        self.assertFalse(log.exists())


@pytest.mark.db
class TestExperiment(unittest.TestCase):
    def setUp(self):
        from app.models.database import Incident, SessionLocal
        self.I, self.Session = Incident, SessionLocal
        self.log = Path(tempfile.mkdtemp()) / "e.jsonl"
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.I).filter(self.I.namespace == NS).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def _incident(self, n, minutes_ahead=15, status="PENDING", outcome=None, detail=None):
        db = self.Session()
        try:
            db.add(self.I(id=f"pytest-e-{n}", pod_name="p", deployment="leak", namespace=NS, risk_score=50,
                          risk_level="MEDIUM", status=status, recommended_action="RESTART_DEPLOYMENT", source="forecast",
                          created_at=(NOW + timedelta(minutes=n)).replace(tzinfo=None),
                          predicted_failure_at=NOW + timedelta(minutes=minutes_ahead + n),
                          life_started_at=NOW + timedelta(minutes=n), outcome=outcome,
                          outcome_detail=json.dumps(detail) if detail else None))
            db.commit()
        finally:
            db.close()

    def test_alternates_and_acts_only_on_treated(self):
        for n in range(4):
            self._incident(n)
        api = FakeAPI()
        made = ex.Experiment(NS, 2, self.log, api).step(now=NOW)
        self.assertEqual([m["group"] for m in made], ["treated", "control", "treated", "control"])
        self.assertEqual(api.calls, [
            ("/api/v1/actions/approve", "pytest-e-0", "RESTART_DEPLOYMENT"),
            ("/api/v1/remediation/execute", "pytest-e-0", "RESTART_DEPLOYMENT"),
            ("/api/v1/actions/approve", "pytest-e-2", "RESTART_DEPLOYMENT"),
            ("/api/v1/remediation/execute", "pytest-e-2", "RESTART_DEPLOYMENT")])
        self.assertEqual(made[0]["execution_status"], "COMPLETED")

    def test_skips_incidents_too_close_or_not_pending(self):
        self._incident(0, minutes_ahead=1)                  # crash under 3 min away
        self._incident(1, status="EXPIRED")
        self.assertEqual(ex.Experiment(NS, 2, self.log, FakeAPI()).step(now=NOW), [])

    def test_resumes_from_its_log_and_stops_at_count(self):
        for n in range(3):
            self._incident(n)
        ex.Experiment(NS, 1, self.log, FakeAPI()).step(now=NOW)        # treated + control, then done
        exp = ex.Experiment(NS, 1, self.log, FakeAPI())
        self.assertTrue(exp.done())
        self.assertEqual(exp.step(now=NOW), [])                       # incident 2 left alone
        self.assertEqual(len(ex.read_log(self.log)), 2)

    def test_failed_approval_never_executes(self):
        self._incident(0)
        api = FakeAPI(approve_ok=False)
        [entry] = ex.Experiment(NS, 1, self.log, api).step(now=NOW)[:1]
        self.assertEqual([c[0] for c in api.calls], ["/api/v1/actions/approve"])
        self.assertEqual(entry["execute_status"], "skipped")

    def test_summary_by_group(self):
        self._incident(0, outcome="AVOIDED", detail={"minutes_ahead": 15.0})
        self._incident(1, outcome="CONFIRMED", detail={"prediction_error_s": 4})
        ex.Experiment(NS, 1, self.log, FakeAPI()).step(now=NOW - timedelta(minutes=1))
        text = ex.summary(self.log)
        self.assertIn("treated    1 incidents: AVOIDED 1", text)
        self.assertIn("control    1 incidents: CONFIRMED 1", text)
        self.assertIn("median 15.0 min", text)

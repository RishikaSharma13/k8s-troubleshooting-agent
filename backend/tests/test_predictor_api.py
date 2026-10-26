import unittest

from fastapi.testclient import TestClient

from app.api import predictor_routes
from app.main import app
from app.predictor.risk_manager import RiskManager
from app.predictor.trend_detector import TrendDetector


class FakeCollector:
    """Controlled collector for API tests; does not call Kubernetes."""

    def __init__(self):
        self.risk_manager = RiskManager()
        self.trend_detector = TrendDetector()

    def scan_namespace(self, namespace):
        return {
            "status": "success",
            "namespace": namespace,
            "pods_scanned": 1,
            "risks": [],
        }

    def get_pending_incidents(self):
        return self.risk_manager.get_pending_incidents()


class FakeExecutor:
    """Stand-in for RemediationExecutor: records what would have run; never touches a cluster."""

    def __init__(self):
        self.calls = []

    def execute_action(self, incident, action_type):
        self.calls.append(action_type)
        return {"status": "COMPLETED", "execution_id": "exec-test-1"}


def _clear_test_database():
    """These tests run only against a *_test database (see conftest.py), so a full cleanup is safe.
    Earlier tests left incidents behind, which made the pending-incidents count unreliable."""
    from app.models.database import Execution, Incident, MetricsHistory, SessionLocal
    db = SessionLocal()
    try:
        db.query(Execution).delete()
        db.query(Incident).delete()
        db.query(MetricsHistory).filter(MetricsHistory.pod_name == "api-test-pod").delete()
        db.commit()
    finally:
        db.close()


class TestPredictorAPI(unittest.TestCase):
    def setUp(self):
        _clear_test_database()
        self.original_collector = predictor_routes.collector
        self.original_executor = predictor_routes.remediation_executor
        self.executor = FakeExecutor()
        predictor_routes.remediation_executor = self.executor
        self.collector = FakeCollector()
        predictor_routes.collector = self.collector
        self.client = TestClient(app)

        # Create a high-risk incident with a rising metric history.
        trend = None
        for value in (35, 50, 70, 90):
            trend = self.collector.risk_manager.update_pod_metrics(
                namespace="default",
                pod_name="api-test-pod",
                deployment_name="api-test-deployment",
                memory_percent=value,
            )

        self.incident = self.collector.risk_manager.create_incident(
            trend_data=trend,
            recommended_action="INVESTIGATE_POD",
            confidence=80,
        )

    def tearDown(self):
        predictor_routes.collector = self.original_collector
        predictor_routes.remediation_executor = self.original_executor
        _clear_test_database()

    def _status(self):
        """Read the incident back from the database (self.incident is only a copy made at creation)."""
        return self.collector.risk_manager.get_incident(self.incident.id)

    def test_predictor_health(self):
        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")
        self.assertEqual(response.json()["module"], "predictor")

    def test_scan_namespace(self):
        response = self.client.get(
            "/api/v1/risks/scan-namespace",
            params={"namespace": "default"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["namespace"], "default")
        self.assertEqual(response.json()["pods_scanned"], 1)

    def test_pending_incidents_endpoint(self):
        response = self.client.get("/api/v1/risks/pending-incidents")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["incidents"][0]["id"], self.incident.id)

    def test_recommend_action_for_existing_incident(self):
        response = self.client.post(
            "/api/v1/actions/recommend",
            params={"incident_id": self.incident.id},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["incident_id"], self.incident.id)
        self.assertIsNotNone(body["recommendation"])
        self.assertEqual(
            body["recommendation"]["action"],
            "SCALE_DEPLOYMENT",
        )

    def test_recommend_action_for_missing_incident(self):
        response = self.client.post(
            "/api/v1/actions/recommend",
            params={"incident_id": "missing-incident"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "error")
        self.assertIsNone(body["recommendation"])

    def test_approve_action(self):
        response = self.client.post(
            "/api/v1/actions/approve",
            params={
                "incident_id": self.incident.id,
                "action": "SCALE_DEPLOYMENT",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["new_status"], "APPROVED")
        self.assertEqual(response.json()["approved_action"], "SCALE_DEPLOYMENT")
        stored = self._status()
        self.assertEqual((stored.status, stored.approved_action), ("APPROVED", "SCALE_DEPLOYMENT"))

    def test_approve_rejects_unknown_action(self):
        response = self.client.post(
            "/api/v1/actions/approve",
            params={
                "incident_id": self.incident.id,
                "action": "DELETE_CLUSTER",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(self._status().status, "PENDING")

    def test_reject_action(self):
        response = self.client.post(
            "/api/v1/actions/reject",
            params={
                "incident_id": self.incident.id,
                "reason": "Manual review required",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["new_status"], "REJECTED")
        self.assertEqual(body["reason"], "Manual review required")
        self.assertEqual(self._status().status, "REJECTED")

    def test_approve_requires_an_action(self):
        response = self.client.post("/api/v1/actions/approve", params={"incident_id": self.incident.id})
        self.assertEqual(response.status_code, 422)          # the action is a required parameter
        self.assertEqual(self._status().status, "PENDING")

    def _approve(self, action):
        r = self.client.post("/api/v1/actions/approve", params={"incident_id": self.incident.id, "action": action})
        self.assertEqual(r.json()["status"], "success")

    def test_execute_runs_only_the_approved_action(self):
        self._approve("SCALE_DEPLOYMENT")
        response = self.client.post("/api/v1/remediation/execute",
                                    params={"incident_id": self.incident.id, "action": "SCALE_DEPLOYMENT"})
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(self.executor.calls, ["SCALE_DEPLOYMENT"])

    def test_execute_refuses_a_different_action(self):
        self._approve("SCALE_DEPLOYMENT")
        response = self.client.post("/api/v1/remediation/execute",
                                    params={"incident_id": self.incident.id, "action": "RESTART_DEPLOYMENT"})
        self.assertEqual(response.json()["status"], "error")
        self.assertIn("does not match the approved action SCALE_DEPLOYMENT", response.json()["error"])
        self.assertEqual(self.executor.calls, [])              # nothing ran
        self.assertEqual(self._status().status, "APPROVED")   # still awaiting the approved action

    def test_execute_refuses_approval_without_recorded_action(self):
        # An incident approved the old way (status only, e.g. before this change)
        self.collector.risk_manager.update_incident_status(self.incident.id, "APPROVED")
        response = self.client.post("/api/v1/remediation/execute",
                                    params={"incident_id": self.incident.id, "action": "SCALE_DEPLOYMENT"})
        self.assertEqual(response.json()["status"], "error")
        self.assertIn("re-approve", response.json()["error"])
        self.assertEqual(self.executor.calls, [])

    def test_execute_refuses_unapproved_incident(self):
        response = self.client.post("/api/v1/remediation/execute",
                                    params={"incident_id": self.incident.id, "action": "SCALE_DEPLOYMENT"})
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(self.executor.calls, [])

    def test_reject_missing_incident(self):
        response = self.client.post(
            "/api/v1/actions/reject",
            params={"incident_id": "missing-incident"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "error")


if __name__ == "__main__":
    unittest.main()

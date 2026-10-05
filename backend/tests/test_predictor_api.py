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


class TestPredictorAPI(unittest.TestCase):
    def setUp(self):
        self.original_collector = predictor_routes.collector
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
        self.assertEqual(self.incident.status, "APPROVED")

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
        self.assertEqual(self.incident.status, "PENDING")

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
        self.assertEqual(self.incident.status, "REJECTED")

    def test_reject_missing_incident(self):
        response = self.client.post(
            "/api/v1/actions/reject",
            params={"incident_id": "missing-incident"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "error")


if __name__ == "__main__":
    unittest.main()

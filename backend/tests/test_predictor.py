
import unittest

from app.predictor.trend_detector import TrendDetector
from app.predictor.risk_manager import RiskManager


class TestTrendDetector(unittest.TestCase):

    def test_empty_values_are_stable(self):
        slope, direction = TrendDetector.calculate_trend([])
        self.assertEqual(slope, 0.0)
        self.assertEqual(direction, "stable")

    def test_single_value_is_stable(self):
        slope, direction = TrendDetector.calculate_trend([45])
        self.assertEqual(slope, 0.0)
        self.assertEqual(direction, "stable")

    def test_rising_trend(self):
        slope, direction = TrendDetector.calculate_trend([35, 42, 50, 61, 74])
        self.assertAlmostEqual(slope, 9.75)
        self.assertEqual(direction, "rising")

    def test_falling_trend(self):
        slope, direction = TrendDetector.calculate_trend([80, 70, 60, 50])
        self.assertAlmostEqual(slope, -10.0)
        self.assertEqual(direction, "falling")

    def test_stable_trend(self):
        slope, direction = TrendDetector.calculate_trend([50, 51, 50, 51])
        self.assertAlmostEqual(slope, 1 / 3)
        self.assertEqual(direction, "stable")

    def test_risk_score_for_normal_usage(self):
        score = TrendDetector.calculate_risk_score(50, 0, 0)
        self.assertEqual(score, 0.0)

    def test_risk_score_for_rising_usage(self):
        score = TrendDetector.calculate_risk_score(74, 9.75, 0)
        self.assertAlmostEqual(score, 44.0)

    def test_risk_score_is_capped_at_100(self):
        score = TrendDetector.calculate_risk_score(100, 20, 10)
        self.assertEqual(score, 100.0)

    def test_risk_levels(self):
        expected = {
            0: "NORMAL",
            20: "LOW",
            40: "MEDIUM",
            60: "HIGH",
            75: "CRITICAL",
        }

        for score, level in expected.items():
            with self.subTest(score=score):
                self.assertEqual(
                    TrendDetector.get_risk_level(score), level
                )


class TestRiskManager(unittest.TestCase):

    def setUp(self):
        self.manager = RiskManager()

    def test_first_metric_creates_trend(self):
        trend = self.manager.update_pod_metrics(
            namespace="default",
            pod_name="demo-pod",
            deployment_name="demo-deployment",
            memory_percent=35,
        )

        self.assertEqual(trend.risk_score, 0.0)
        self.assertEqual(trend.risk_level, "NORMAL")
        self.assertEqual(len(trend.metrics_history), 1)

    def test_metrics_history_is_limited(self):
        for value in range(10, 25):
            trend = self.manager.update_pod_metrics(
                namespace="default",
                pod_name="demo-pod",
                deployment_name="demo-deployment",
                memory_percent=value,
            )

        self.assertEqual(
            len(trend.metrics_history), self.manager.max_history
        )
        self.assertEqual(trend.metrics_history[-1].value, 24)

    def test_invalid_memory_percentage_is_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.update_pod_metrics(
                namespace="default",
                pod_name="demo-pod",
                deployment_name="demo-deployment",
                memory_percent=101,
            )

    def test_negative_restart_count_is_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.update_pod_metrics(
                namespace="default",
                pod_name="demo-pod",
                deployment_name="demo-deployment",
                memory_percent=50,
                restart_count=-1,
            )

    def test_incident_creation_and_retrieval(self):
        trend = self.manager.update_pod_metrics(
            namespace="default",
            pod_name="demo-pod",
            deployment_name="demo-deployment",
            memory_percent=80,
        )

        incident = self.manager.create_incident(
            trend_data=trend,
            recommended_action="INVESTIGATE_POD",
            confidence=80,
        )

        self.assertEqual(incident.status, "PENDING")
        self.assertEqual(self.manager.get_incident(incident.id), incident)
        self.assertIn(incident, self.manager.get_pending_incidents())

    def test_incident_status_update(self):
        trend = self.manager.update_pod_metrics(
            namespace="default",
            pod_name="demo-pod",
            deployment_name="demo-deployment",
            memory_percent=80,
        )
        incident = self.manager.create_incident(
            trend_data=trend,
            recommended_action="INVESTIGATE_POD",
            confidence=80,
        )

        updated = self.manager.update_incident_status(
            incident.id, "APPROVED"
        )

        self.assertTrue(updated)
        self.assertEqual(incident.status, "APPROVED")
        self.assertNotIn(incident, self.manager.get_pending_incidents())

    def test_unknown_incident_returns_false(self):
        self.assertFalse(
            self.manager.update_incident_status(
                "missing-id", "APPROVED"
            )
        )

    def test_invalid_incident_status_is_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.update_incident_status(
                "missing-id", "UNKNOWN"
            )


if __name__ == "__main__":
    unittest.main()

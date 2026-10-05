
import unittest

from app.predictor.trend_detector import TrendDetector
from app.predictor.risk_manager import RiskManager
from app.predictor.metrics_collector import MetricsCollector

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

    def test_incident_can_be_rejected(self):
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
            incident.id, "REJECTED"
        )

        self.assertTrue(updated)
        self.assertEqual(incident.status, "REJECTED")
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




class TestActionPlanner(unittest.TestCase):
    from app.predictor.action_planner import ActionPlanner, ActionType

    def test_no_action_below_threshold(self):
        result = self.ActionPlanner.propose_action(19, "NORMAL", 0, 45, 0)
        self.assertIsNone(result)

    def test_high_memory_rising_trend_recommends_scale(self):
        result = self.ActionPlanner.propose_action(45, "MEDIUM", 2, 75, 3)
        self.assertEqual(result["action"], "SCALE_DEPLOYMENT")
        self.assertEqual(result["parameters"]["scale_increase_percent"], 50)
        self.assertEqual(result["confidence"], 80.0)

    def test_high_memory_with_restarts_recommends_memory_limit(self):
        result = self.ActionPlanner.propose_action(45, "MEDIUM", 4, 85, 0)
        self.assertEqual(result["action"], "INCREASE_MEMORY_LIMIT")
        self.assertEqual(result["parameters"]["memory_limit_increase_percent"], 30)
        self.assertEqual(result["confidence"], 75.0)

    def test_excessive_restarts_recommends_log_investigation(self):
        result = self.ActionPlanner.propose_action(35, "LOW", 6, 50, 0)
        self.assertEqual(result["action"], "INVESTIGATE_LOGS")
        self.assertEqual(result["parameters"]["lines"], 100)
        self.assertEqual(result["confidence"], 90.0)

    def test_generic_high_risk_recommends_scale(self):
        result = self.ActionPlanner.propose_action(60, "HIGH", 0, 50, 0)
        self.assertEqual(result["action"], "SCALE_DEPLOYMENT")
        self.assertEqual(result["parameters"]["scale_increase_percent"], 30)
        self.assertEqual(result["confidence"], 65.0)

    def test_medium_risk_recommends_log_investigation(self):
        result = self.ActionPlanner.propose_action(40, "MEDIUM", 0, 50, 0)
        self.assertEqual(result["action"], "INVESTIGATE_LOGS")
        self.assertEqual(result["parameters"]["lines"], 50)
        self.assertEqual(result["confidence"], 70.0)

    def test_low_detected_risk_recommends_short_log_review(self):
        result = self.ActionPlanner.propose_action(20, "LOW", 0, 50, 0)
        self.assertEqual(result["action"], "INVESTIGATE_LOGS")
        self.assertEqual(result["parameters"]["lines"], 30)
        self.assertEqual(result["confidence"], 50.0)

    def test_validate_action_accepts_known_action(self):
        self.assertTrue(self.ActionPlanner.validate_action("SCALE_DEPLOYMENT"))

    def test_validate_action_rejects_unknown_action(self):
        self.assertFalse(self.ActionPlanner.validate_action("DELETE_CLUSTER"))

class TestMetricsCollectorIncidents(unittest.TestCase):

    def _create_collector(self):
        collector = MetricsCollector()

        collector._get_pods_in_namespace = lambda namespace: [
            {
                "name": "crash-test-pod",
                "deployment": "crash-test",
                "namespace": namespace,
                "status": "Running",
                "restart_count": 5,
            }
        ]

        return collector

    def test_qualifying_risk_creates_pending_incident(self):
        collector = self._create_collector()

        result = collector.scan_namespace("default")

        self.assertEqual(result["pods_scanned"], 1)
        self.assertEqual(len(result["risks"]), 1)

        incidents = collector.get_pending_incidents()

        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["status"], "PENDING")
        self.assertEqual(
            incidents[0]["pod_name"],
            "crash-test-pod",
        )

    def test_repeated_scan_does_not_create_duplicate_incident(self):
        collector = self._create_collector()

        first_result = collector.scan_namespace("default")
        second_result = collector.scan_namespace("default")

        self.assertEqual(len(first_result["risks"]), 1)
        self.assertEqual(len(second_result["risks"]), 1)

        incidents = collector.get_pending_incidents()

        self.assertEqual(len(incidents), 1)


if __name__ == "__main__":
    unittest.main()

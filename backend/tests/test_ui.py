"""Tests for GET /ui (dashboard + approval panel) and the cross-site guard on POST endpoints."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestCrossSiteGuard(unittest.TestCase):
    def test_other_site_is_refused_before_anything_runs(self):
        r = client.post("/api/v1/actions/approve", params={"incident_id": "x", "action": "RESTART_DEPLOYMENT"},
                        headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = client.post("/api/v1/remediation/execute", params={"incident_id": "x", "action": "RESTART_DEPLOYMENT"},
                        headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_misleading_auto_remediate_endpoint_is_gone(self):
        r = client.post("/api/v1/actions/auto-remediate", params={"pod_name": "p"})
        self.assertIn(r.status_code, (404, 405))


@pytest.mark.db
def test_same_site_and_no_origin_pass_the_guard():
    """Passes the guard, then reaches the endpoint (which reads the database, hence the db mark)."""
    for headers in ({}, {"Origin": "http://testserver"}):
        r = client.post("/api/v1/actions/approve", params={"incident_id": "no-such", "action": "RESTART_DEPLOYMENT"},
                        headers=headers)
        assert r.status_code != 403, headers


@pytest.mark.db
class TestUI(unittest.TestCase):
    def setUp(self):
        from app.models.database import Incident, SessionLocal
        self.I, self.Session = Incident, SessionLocal
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        import os
        os.environ.pop("DASHBOARD_HTML", None)

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.I).filter(self.I.id.like("pytest-ui-%")).delete(synchronize_session=False)
            from app.models.database import MemorySample
            db.query(MemorySample).filter(MemorySample.namespace == "remediate-lab",
                                          MemorySample.pod.in_(["p", "pod-gone", "pod-live"])).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def _incident(self, iid, minutes, source="forecast", status="PENDING", reasoning="Forecast (linear): +12.0 MiB/min",
                  pod="p"):
        now = datetime.now(timezone.utc)
        db = self.Session()
        try:
            db.add(self.I(id=iid, pod_name=pod, deployment="leak-remediate", namespace="remediate-lab", risk_score=50,
                          risk_level="MEDIUM", status=status, recommended_action="RESTART_DEPLOYMENT", source=source,
                          predicted_failure_at=now + timedelta(minutes=minutes), life_started_at=now, reasoning=reasoning))
            db.commit()
        finally:
            db.close()

    def _reading(self, pod, seconds_ago):
        from app.models.database import MemorySample
        db = self.Session()
        try:
            db.add(MemorySample(namespace="remediate-lab", pod=pod, container="memload", deployment="leak-remediate",
                                metric_timestamp=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
                                window_seconds=15, memory_bytes=30 * 1048576, memory_limit_bytes=256 * 1048576))
            db.commit()
        finally:
            db.close()

    def test_stale_and_superseded_incidents_are_not_offered(self):
        """Seen on 18 Nov: after a reset, the replaced pod's incident was still listed beside the new one."""
        self._incident("pytest-ui-stale", 12, pod="pod-gone")
        self._reading("pod-gone", 600)                             # last reported 10 minutes ago: replaced
        self._incident("pytest-ui-live", 15, pod="pod-live")
        self._reading("pod-live", 20)
        page = client.get("/ui").text
        self.assertIn("pytest-ui-live", page)
        self.assertNotIn("pytest-ui-stale", page)

    def test_panel_lists_only_actionable_forecast_incidents(self):
        self._incident("pytest-ui-ok", 15, reasoning="<script>alert(1)</script> leak")
        self._reading("p", 20)
        self._incident("pytest-ui-close", 1)                       # under 3 minutes: too late to act
        self._incident("pytest-ui-old", 15, source=None)          # not from a forecast
        self._incident("pytest-ui-done", 15, status="EXECUTING")
        page = client.get("/ui").text
        self.assertIn("Incidents awaiting approval", page)
        self.assertIn("pytest-ui-ok", page)
        for absent in ("pytest-ui-close", "pytest-ui-old", "pytest-ui-done"):
            self.assertNotIn(absent, page)
        self.assertIn("Approve and execute: planned restart", page)
        self.assertIn("raise memory limit (+50%) instead", page)
        self.assertNotIn("<script>alert(1)</script>", page)       # reasoning is escaped
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_wraps_the_generated_dashboard(self):
        d = Path(tempfile.mkdtemp()) / "dashboard.html"
        d.write_text('<html><head><meta http-equiv="refresh" content="60"><title>t</title></head><body>'
                     '<div class="tiles">T</div><div class="card"><h2>1. Live forecast</h2></div></body></html>')
        import os
        os.environ["DASHBOARD_HTML"] = str(d)
        page = client.get("/ui").text
        self.assertNotIn('http-equiv="refresh"', page)            # the script refreshes, pausing during actions
        self.assertLess(page.index('<div class="tiles">'), page.index("Incidents awaiting approval"))
        self.assertLess(page.index("Incidents awaiting approval"), page.index("1. Live forecast"))
        self.assertIn("async function act(", page)
        d.write_text("<html><head></head><body><div class='card'>first</div></body></html>")   # other quote style
        page = client.get("/ui").text
        self.assertLess(page.index("Incidents awaiting approval"), page.index("first"))

    def test_works_without_a_generated_dashboard(self):
        page = client.get("/ui").text
        self.assertIn("has not been generated yet", page)
        self.assertIn("No incidents awaiting approval", page)

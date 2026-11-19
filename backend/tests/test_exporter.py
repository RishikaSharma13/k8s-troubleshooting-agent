"""Tests for the read-only Prometheus exporter."""
import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from app.metrics import exporter as ex

NS = "pytest-exporter"
NOW = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
MiB = 1024 * 1024
LINE = re.compile(r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{([a-zA-Z_][a-zA-Z0-9_]*="([^"\\]|\\.)*",?)*\})? -?[0-9.e+-]+$')


def test_http_server_serves_metrics_only():
    server = ex.make_server("127.0.0.1", 0, lambda: "# HELP k8s_failure_x x\n# TYPE k8s_failure_x gauge\nk8s_failure_x 1\n")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        r = urllib.request.urlopen(base + "/metrics")
        assert r.status == 200 and r.headers["Content-Type"].startswith("text/plain; version=0.0.4")
        assert b"k8s_failure_x 1" in r.read()
        for path, method in (("/nothing", "GET"), ("/metrics", "POST")):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None))
            assert e.value.code in (404, 501)                        # nothing but GET /metrics and /health
    finally:
        server.shutdown()


def test_cache_reuses_the_last_result():
    calls = []
    c = ex.Cache(lambda: calls.append(1) or "x", ttl=60)
    assert c.get() == c.get() == "x" and len(calls) == 1


@pytest.mark.db
class TestCollect(unittest.TestCase):
    def setUp(self):
        from app.models.database import Execution, Incident, MemorySample, SessionLocal
        self.E, self.I, self.M, self.Session = Execution, Incident, MemorySample, SessionLocal
        self._cleanup()
        db = self.Session()
        try:
            for i in range(40):                                       # 10 minutes of a 12 MiB/min leak, ending at 180 MiB
                t = NOW - timedelta(seconds=15 * (39 - i))
                db.add(self.M(namespace=NS, pod="leak-pod", container="memload", deployment="leak", metric_timestamp=t,
                              window_seconds=15, memory_bytes=int((180 - 3 * (39 - i)) * MiB), memory_limit_bytes=256 * MiB))
            db.add(self.I(id="pytest-x-1", pod_name="leak-pod", deployment="leak", namespace=NS, risk_score=50,
                          risk_level="HIGH", status="RESOLVED", recommended_action="RESTART_DEPLOYMENT", source="forecast",
                          predicted_failure_at=NOW, life_started_at=NOW, outcome="AVOIDED"))
            db.flush()
            db.add(self.E(id="pytest-x-e", incident_id="pytest-x-1", action="RESTART_DEPLOYMENT", status="SUCCESS"))
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.E).filter(self.E.incident_id.like("pytest-x-%")).delete(synchronize_session=False)
            db.query(self.I).filter(self.I.id.like("pytest-x-%")).delete(synchronize_session=False)
            db.query(self.M).filter(self.M.namespace == NS).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def _value(self, text, name):
        m = re.search(rf'^k8s_failure_{name}\{{namespace="{NS}",deployment="leak",pod="leak-pod"\}} (\S+)$', text, re.M)
        return float(m.group(1)) if m else None

    def test_metrics_are_well_formed_and_correct(self):
        import os
        os.environ["CALIBRATION_FILE"] = "/nonexistent.json"
        try:
            text = ex.collect(now=NOW, namespaces=[NS])
        finally:
            os.environ.pop("CALIBRATION_FILE")
        for line in text.splitlines():
            assert line.startswith("# HELP ") or line.startswith("# TYPE ") or LINE.match(line), line
        assert 360 <= self._value(text, "seconds_to_limit") <= 400   # (256 - 180) MiB at 12 MiB/min = 380 s
        assert abs(self._value(text, "memory_usage_ratio") - 180 / 256) < 0.01
        assert f'k8s_failure_forecast_status{{namespace="{NS}",deployment="leak",pod="leak-pod",status="crossing_predicted"}} 1' in text
        assert self._value(text, "seconds_to_limit_lower") is None   # no calibration file: no range
        assert 'k8s_failure_incidents{outcome="AVOIDED"}' in text
        assert 'k8s_failure_executions{action="RESTART_DEPLOYMENT",status="SUCCESS"}' in text

    def test_calibrated_bounds_when_calibrated(self):
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "c.json")
        open(path, "w").write(json.dumps({"method": "linear", "buckets_s": [0, 300, 600, 1200, 1800],
                                          "bounds_s": [[-4, -1], [-5, 0], [-9, 5], [-8, 0]]}))
        os.environ["CALIBRATION_FILE"] = path
        try:
            text = ex.collect(now=NOW, namespaces=[NS])
        finally:
            os.environ.pop("CALIBRATION_FILE")
        lo, mid, hi = (self._value(text, n) for n in ("seconds_to_limit_lower", "seconds_to_limit", "seconds_to_limit_upper"))
        assert lo is not None and lo <= mid + 1 and hi >= lo                # 5-10 min ahead: range -5 to 0 s

"""Tests for the dashboard generator.

Importing the module here means a syntax error (e.g. Python-3.12-only syntax on the
project's Python 3.10) fails the test suite instead of surfacing at demo time.
"""
import re
import unittest
from datetime import datetime, timedelta, timezone

import pytest

from app.predictor import dashboard
from app.predictor.evaluate_signals import JobLife, evaluate_job, summarize_jobs, write_report

MiB = 1024 * 1024


def current_format_report(path):
    """A Job report written by the real report writer, so the test follows any column change."""
    t0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    late = JobLife("deadline-job", "late", [t0 + timedelta(seconds=17 * i) for i in range(17)],
                   [float(i) for i in range(17)], 20.0, t0 + timedelta(seconds=285), "DeadlineExceeded",
                   t0 + timedelta(seconds=285))
    ok = JobLife("deadline-job", "ok", [t0 + timedelta(seconds=12 * i) for i in range(20)],
                 [float(i) for i in range(20)], 20.0, t0 + timedelta(seconds=285), "Complete", t0 + timedelta(seconds=240))
    write_report(path, "predict-lab", None, None, ([late, ok], summarize_jobs(evaluate_job(late, ["linear"]) +
                                                                           evaluate_job(ok, ["linear"]))))


class TestMemoryFacts(unittest.TestCase):
    def test_numbers_come_from_the_report(self):
        import pathlib, tempfile
        path = pathlib.Path(tempfile.mkdtemp()) / "mem.md"
        path.write_text(
            "Generated 2026-10-26 05:37 UTC. Parameters: origin_step_s=60.0, since=2026-10-21T05:51, until=2026-10-26T04:27\n\n"
            "## leak-slow\n\n| Method | OOM lives | Coverage @10m | Coverage @5m |\n|---|---|---|---|\n"
            "| linear | 145 | 64% | 56% |\n| prophet | 145 | 51% | 45% |\n\n"
            "## leak-steady\n\n| Method | OOM lives | Coverage @10m | Coverage @5m |\n|---|---|---|---|\n"
            "| linear | 292 | 76% | 93% |\n| prophet | 292 | 66% | 80% |\n")
        facts = dashboard.memory_report_facts(str(path), dashboard.parse_report(str(path)))
        self.assertEqual(facts["n_oom"], 437)
        self.assertEqual(facts["coverage"], "45-93%")
        self.assertEqual(facts["window"], "data from 2026-10-21T05:51 to 2026-10-26T04:27 UTC")


class TestSummary(unittest.TestCase):
    def test_columns_are_read_by_name(self, tmp_path=None):
        import tempfile, pathlib
        path = pathlib.Path(tempfile.mkdtemp()) / "report.md"
        current_format_report(path)
        html = dashboard.signals_summary(str(path))
        row = re.search(r"<tr><td>linear</td>(.*?)</tr>", html).group(1)
        cells = re.findall(r"<td>(.*?)</td>", row)
        # late jobs, missed, warning, accuracy@50%, accuracy@75%: accuracy cells must hold accuracies
        self.assertEqual(cells[0], "1")
        self.assertTrue(cells[3].startswith("100%") and "TP" in cells[3], cells)
        self.assertTrue(cells[4].startswith("100%") and "TP" in cells[4], cells)

    def test_sections_without_data(self):
        html = dashboard.new_prediction_sections(None, None, None, None, "")
        self.assertIn("No storage readings yet", html)
        self.assertIn("No job readings yet", html)
        self.assertNotIn("6. Evaluation", html)


@pytest.mark.db
class TestFullPage(unittest.TestCase):
    """Builds the whole page from the test database (namespace predict-lab is used only by tests there)."""

    def setUp(self):
        from app.models.database import (ContainerTermination, MemorySample, OutcomeEvent, SessionLocal,
                                         SignalSample)
        self.models = (MemorySample, ContainerTermination, SignalSample, OutcomeEvent)
        self.Session = SessionLocal
        self._cleanup()
        now = datetime.now(timezone.utc)
        db = self.Session()
        try:
            for i in range(60):
                t = now - timedelta(seconds=15 * (59 - i))
                db.add(MemorySample(namespace="predict-lab", pod="leak-steady-x", container="memload",
                                    deployment="leak-steady", metric_timestamp=t, memory_bytes=int((20 + 3 * i) * MiB),
                                    memory_limit_bytes=256 * MiB))
            for i in range(8):
                db.add(SignalSample(namespace="predict-lab", workload_kind="Deployment", workload="disk-leak",
                                    object_name="disk-leak-x", signal="ephemeral_storage_bytes",
                                    observed_at=now - timedelta(seconds=80 * (7 - i)), value=(10 + 5 * i) * MiB,
                                    limit_value=100 * MiB))
            start = now - timedelta(seconds=100)
            for i in range(7):
                db.add(SignalSample(namespace="predict-lab", workload_kind="CronJob", workload="deadline-job",
                                    object_name="deadline-job-1", signal="job_succeeded",
                                    observed_at=start + timedelta(seconds=14 * i), value=float(i), limit_value=20,
                                    deadline_at=start + timedelta(seconds=285)))
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            for m in self.models:
                db.query(m).filter(m.namespace == "predict-lab").delete()
            db.commit()
        finally:
            db.close()

    def test_page_is_written(self):
        import pathlib, sys, tempfile
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "mem.md").write_text("## leak-steady\n\n| Method | OOM lives | Missed | Lead time median (min) |\n"
                                  "|---|---|---|---|\n| linear | 1 | 0 | 18.5 |\n")
        argv = sys.argv
        sys.argv = ["dashboard", "--out", str(d / "page.html"), "--report", str(d / "mem.md")]
        try:
            dashboard.main()
        finally:
            sys.argv = argv
        page = (d / "page.html").read_text()
        for section in ("1. Live forecast", "4. Storage", "5. Jobs"):
            self.assertIn(section, page)
        self.assertIn("disk-leak", page)
        self.assertIn("deadline-job-1", page)


@pytest.mark.db
class TestRemediationSection(unittest.TestCase):
    """Section 7: incidents, verdicts and executions, from the test database."""

    def setUp(self):
        from app.models.database import Execution, Incident, SessionLocal
        self.E, self.I, self.Session = Execution, Incident, SessionLocal
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.E).filter(self.E.incident_id.like("pytest-d-%")).delete(synchronize_session=False)
            db.query(self.I).filter(self.I.id.like("pytest-d-%")).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def test_empty_when_no_forecast_incidents(self):
        self.assertEqual(dashboard.remediation_section(), "")

    def test_verdicts_errors_and_evidence(self):
        import json as _json
        p = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)
        rows = []
        for n, (outcome, status, detail) in enumerate([
                ("CONFIRMED", "EXPIRED", {"prediction_error_s": 25, "minutes_ahead": 18.5}),
                ("AVOIDED", "RESOLVED", {"minutes_ahead": 15.3}),
                (None, "PENDING", {})]):
            rows.append(self.I(id=f"pytest-d-{n}", pod_name="leak-p", deployment="leak-remediate", namespace="ns",
                               risk_score=50, risk_level="HIGH", status=status, recommended_action="RESTART_DEPLOYMENT",
                               source="forecast", predicted_failure_at=p + timedelta(minutes=20 * n),
                               life_started_at=p + timedelta(minutes=20 * n), outcome=outcome,
                               outcome_detail=_json.dumps(detail) if detail else None))
        rows.append(self.E(id="pytest-d-1-x", incident_id="pytest-d-1", action="RESTART_DEPLOYMENT", status="SUCCESS",
                           executed_at=datetime(2026, 11, 3, 8, 5), evidence=_json.dumps({
                               "command": "kubectl patch deployment/leak-remediate -n remediate-lab ...",
                               "dry_run": "deployment.apps/leak-remediate patched",
                               "verification": {"outcome": "AVOIDED"}})))
        rows.append(self.E(id="pytest-d-2-x", incident_id="pytest-d-2", action="RESTART_DEPLOYMENT", status="REFUSED",
                           executed_at=datetime(2026, 11, 3, 7, 50), error_message="server-side dry run failed",
                           evidence=_json.dumps({"command": "kubectl rollout restart deployment/leak-remediate"})))
        db = self.Session()
        try:
            for r in rows:
                db.add(r)
                db.flush()
            db.commit()
        finally:
            db.close()
        html = dashboard.remediation_section()
        for text in ("7. Closing the loop", "<b>AVOIDED</b>: 1", "<b>CONFIRMED</b>: 1", "median error <b>25 s</b>", "predicted a median <b>18.5 min</b> ahead",
                     "error +25 s", "acted 15.3 min ahead", "awaiting approval", "kubectl patch deployment/leak-remediate",
                     "histogram of live prediction errors", "refused: nothing ran"):
            self.assertIn(text, html)


@pytest.mark.db
class TestSummaryTiles(unittest.TestCase):
    """The headline strip: every figure comes from the report, the database or the experiment log."""

    def setUp(self):
        from app.models.database import Execution, Incident, SessionLocal
        self.E, self.I, self.Session = Execution, Incident, SessionLocal
        self._cleanup()
        import json as _json, pathlib, tempfile
        d = pathlib.Path(tempfile.mkdtemp())
        self.report = d / "mem.md"
        self.report.write_text(
            "## flat-hot\n\n| Method | OOM lives | False-alarm rate | Lead time median (min) |\n|---|---|---|---|\n"
            "| linear | 0 | 0% | - |\n| threshold80 | 0 | 51% | - |\n\n"
            "## leak-slow\n\n| Method | OOM lives | False-alarm rate | Lead time median (min) |\n|---|---|---|---|\n"
            "| linear | 145 | - | 29.2 |\n| threshold80 | 145 | - | 7.9 |\n\n"
            "## leak-steady\n\n| Method | OOM lives | False-alarm rate | Lead time median (min) |\n|---|---|---|---|\n"
            "| linear | 292 | - | 18.5 |\n| threshold80 | 292 | - | 3.6 |\n")
        p = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)
        db = self.Session()
        try:
            for n, (o, det) in enumerate([("CONFIRMED", {"prediction_error_s": 10}), ("CONFIRMED", {"prediction_error_s": -20}),
                                          ("AVOIDED", {"minutes_ahead": 17.8})]):
                db.add(self.I(id=f"pytest-t-{n}", pod_name="p", deployment="leak-remediate", namespace="ns", risk_score=50,
                              risk_level="HIGH", status="EXPIRED", recommended_action="RESTART_DEPLOYMENT", source="forecast",
                              predicted_failure_at=p + timedelta(minutes=20 * n), life_started_at=p + timedelta(minutes=20 * n),
                              outcome=o, outcome_detail=_json.dumps(det)))
            db.flush()
            db.add(self.E(id="pytest-t-x1", incident_id="pytest-t-2", action="RESTART_DEPLOYMENT", status="SUCCESS"))
            db.add(self.E(id="pytest-t-x2", incident_id="pytest-t-1", action="RESTART_DEPLOYMENT", status="REFUSED"))
            db.commit()
        finally:
            db.close()
        self.log = d / "exp.jsonl"
        self.log.write_text('{"incident_id": "pytest-t-2", "group": "treated"}\n{"incident_id": "pytest-t-0", "group": "control"}\n')

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.E).filter(self.E.incident_id.like("pytest-t-%")).delete(synchronize_session=False)
            db.query(self.I).filter(self.I.id.like("pytest-t-%")).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def test_tiles_with_experiment(self):
        html = dashboard.summary_tiles(dashboard.parse_report(str(self.report)), str(self.log))
        for text in ("18.5–29.2 min", "a normal 80% alert: 3.6–7.9 min (437 crashes)", "0%", "a normal 80% alert: 51%",
                     "live predictions came true", "median error 15 s · 0 did not", "1 of 1",
                     "left alone: 1 of 1 crashed as predicted", "1 executed · 1 refused safely"):
            self.assertIn(text, html)

    def test_tiles_without_experiment_and_block(self):
        html = dashboard.summary_tiles(dashboard.parse_report(str(self.report)), None)
        self.assertIn("crashes prevented by approved actions", html)
        block = dashboard.experiment_block(str(self.log))
        self.assertIn("acted on (planned restart)", block)
        self.assertIn("AVOIDED 1", block)
        self.assertEqual(dashboard.experiment_block(None), "")


def test_live_chart_is_not_stretched_right_after_a_restart():
    """Seen on 18 Nov: with only minutes of data, labels at the window start stretched the image."""
    import base64, struct
    from types import SimpleNamespace
    now = datetime.now(timezone.utc)
    times = [now - timedelta(seconds=15 * i) for i in range(20, 0, -1)]          # 5 minutes of data
    life = SimpleNamespace(deployment="leak-steady", times=times, values=[(10 + i) * 1048576 for i in range(20)],
                           ends_in_oom=False, end_time=None)
    png = base64.b64decode(dashboard.chart_recent([life]))
    width = struct.unpack(">I", png[16:20])[0]                                   # PNG header: image width
    assert width < 1400, width


def test_range_text_only_for_the_calibrated_method(tmp_path, monkeypatch):
    import json as _json
    (tmp_path / "c.json").write_text(_json.dumps({"method": "linear", "buckets_s": [0, 300, 600, 1200, 1800],
                                                  "bounds_s": [[-4, -1], [-5, 0], [-9, 5], [-8, 0]]}))
    monkeypatch.setenv("CALIBRATION_FILE", str(tmp_path / "c.json"))
    start = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
    assert "80% range 11:44:51–11:45:05, calibrated" in dashboard.range_text(start, 900, "linear")
    assert dashboard.range_text(start, 900, "prophet") == ""          # Prophet was not calibrated: no range

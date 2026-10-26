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

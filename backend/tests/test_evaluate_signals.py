"""Tests for the storage-eviction and Job-deadline evaluation, on cases with known answers."""
import unittest
from datetime import datetime, timedelta, timezone

import pytest

from app.predictor.evaluate import Life, classify, evaluate_life, summarize
from app.predictor.evaluate_signals import (
    STORAGE_HORIZON_S, STORAGE_LOOKBACK_S, STORAGE_MIN_POINTS, STORAGE_QUALITY, JobLife, evaluate_job, load_job_lives,
    load_storage_lives, summarize_jobs,
)
from app.predictor.memory_forecast import MiB

T0 = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
LIMIT = 100 * MiB


def ts(s):
    return T0 + timedelta(seconds=s)


def disk_leak_life(name="disk-leak-a", mib_per_min=4.0, every_s=80):
    """What the collector stores for disk-leak: a reading each kubelet re-measurement (~80 s);
    evicted at the first measurement that exceeds the limit."""
    times, values, t = [], [], 0
    while True:
        v = 1 + mib_per_min * t / 60
        times.append(ts(t)); values.append(int(v * MiB))
        if v > 100:
            return Life("disk-leak", name, "", name + "#1", times, values, LIMIT, ts(t + 1), "Evicted")
        t += every_s


def disk_flat_life(minutes=60):
    times = [ts(180 * i) for i in range(minutes // 3)]                 # 3-minute re-confirmations of 85 MiB
    return Life("disk-flat", "disk-flat-b", "", "disk-flat-b#1", times, [85 * MiB] * len(times), LIMIT)


class TestStartupStep(unittest.TestCase):
    """disk-flat on the real cluster: two readings at 0 MiB, then 85 MiB from the first minute on."""

    def test_step_misread_as_trend_only_briefly(self):
        times = [ts(0), ts(14), ts(60)] + [ts(60 + 180 * i) for i in range(1, 21)]          # 1 hour
        values = [0, 0] + [85 * MiB] * 21
        flat = Life("disk-flat", "disk-flat-s", "", "disk-flat-s#1", times, values, LIMIT)
        rows = [r for r in evaluate_life(flat, "negative", 60.0, STORAGE_HORIZON_S, STORAGE_LOOKBACK_S, ["linear"], (),
                                         quality=STORAGE_QUALITY, value_gap_s=300, min_points=STORAGE_MIN_POINTS)
                if r.method == "linear"]
        alerting = [(r.origin - ts(0)).total_seconds() for r in rows if r.alert]
        self.assertTrue(all(t <= 15 * 60 for t in alerting))          # only while the step is in the window
        self.assertLess(len(alerting) / len(rows), 0.25)
        # and after that a real answer ("no crossing"), not a refusal
        late = [r for r in rows if (r.origin - ts(0)).total_seconds() > 15 * 60]
        self.assertTrue(late and all(r.status == "no_crossing" for r in late))


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.leak, self.flat = disk_leak_life(), disk_flat_life()
        self.cases = classify([self.leak, self.flat], STORAGE_QUALITY.min_samples)
        rows = []
        for life in (self.leak, self.flat):
            rows += evaluate_life(life, self.cases[life.life_id], 60.0, STORAGE_HORIZON_S, STORAGE_LOOKBACK_S,
                                  ["linear"], (80, 90), quality=STORAGE_QUALITY, value_gap_s=300,
                                  min_points=STORAGE_MIN_POINTS)
        self.s = summarize(rows, STORAGE_HORIZON_S)

    def test_cases(self):
        self.assertEqual(self.cases, {self.leak.life_id: "positive", self.flat.life_id: "negative"})

    def test_forecast_warns_far_earlier_than_thresholds(self):
        lin = self.s[("disk-leak", "linear")]["lead_min_median"]
        t80 = self.s[("disk-leak", "threshold80")]["lead_min_median"]
        self.assertEqual(self.s[("disk-leak", "linear")]["missed"], 0)
        self.assertGreater(lin, 15)                     # alerts once 5 readings (~5 min) exist in a ~25 min life
        self.assertLess(t80, 7)                         # 80 -> 100 MiB at 4 MiB/min is ~5 min, plus coarse readings

    def test_eviction_after_crossing_makes_forecasts_early(self):
        self.assertLess(self.s[("disk-leak", "linear")]["cp600_bias_min"], 0.05)   # early (negative) or ~0, not late

    def test_flat_disk_false_alarms(self):
        self.assertEqual(self.s[("disk-flat", "linear")]["false_alarm_rate"], 0.0)
        self.assertLess(self.s[("disk-flat", "linear")]["no_forecast_share"], 0.3)   # answered, not refused
        self.assertEqual(self.s[("disk-flat", "threshold80")]["false_alarm_rate"], 1.0)   # 85% is always above 80%


def job_life(name, item_s, outcome, completions=20, allowed_s=285):
    """Progress readings as items complete every item_s seconds; ends Complete or at the deadline."""
    times, values, k = [ts(0)], [0.0], 0
    while True:
        t = (k + 1) * item_s
        if t >= allowed_s or k + 1 > completions:
            break
        k += 1
        times.append(ts(t)); values.append(float(k))
    end = ts(completions * item_s) if outcome == "Complete" else ts(allowed_s)
    return JobLife("deadline-job", name, times, values, float(completions), ts(allowed_s), outcome, end)


class TestJobs(unittest.TestCase):
    def setUp(self):
        self.fast = job_life("fast", 12.0, "Complete")             # 20 x 12 s = 240 s < 285 s
        self.slow = job_life("slow", 17.0, "DeadlineExceeded")     # would need 340 s
        rows = evaluate_job(self.fast, ["linear"]) + evaluate_job(self.slow, ["linear"])
        self.s = summarize_jobs(rows)

    def test_cases(self):
        self.assertEqual((self.fast.case, self.slow.case), ("negative", "positive"))

    def test_forecast_flags_late_job_by_halfway_without_false_alarm(self):
        lin = self.s["linear"]
        self.assertEqual((lin["missed"], lin["false_alarm_jobs"]), (0, 0))
        self.assertEqual(lin["cp50"]["accuracy"], 1.0)
        self.assertGreater(lin["warning_s_median"], 150)           # warned with most of the time still left

    def test_threshold_cannot_decide_at_halfway(self):
        t = self.s["threshold80"]
        self.assertEqual((t["cp50"]["tp"], t["cp50"]["fn"]), (0, 1))  # by definition silent before 80%
        self.assertLess(t["warning_s_median"], 0.21 * 285)

    def test_finish_time_error_on_time_job(self):
        self.assertLess(self.s["linear"]["cp50"]["finish_err_s_median"], 15)


@pytest.mark.db
class TestLoaders(unittest.TestCase):
    NS = "pytest-eval-signals"

    def setUp(self):
        from app.models.database import OutcomeEvent, SessionLocal, SignalSample
        self.S, self.O, self.Session = SignalSample, OutcomeEvent, SessionLocal
        self._cleanup()
        db = self.Session()
        try:
            leak = disk_leak_life()
            for t, v in zip(leak.times, leak.values):
                db.add(self.S(namespace=self.NS, workload_kind="Deployment", workload="disk-leak", object_name="disk-leak-a",
                              signal="ephemeral_storage_bytes", observed_at=t, value=v, limit_value=LIMIT))
            db.add(self.O(namespace=self.NS, workload_kind="Deployment", workload="disk-leak", object_name="disk-leak-a",
                          event_type="Evicted", occurred_at=leak.end_time))
            slow = job_life("slow", 17.0, "DeadlineExceeded")
            for t, v in zip(slow.times, slow.values):
                db.add(self.S(namespace=self.NS, workload_kind="CronJob", workload="deadline-job", object_name="slow",
                              signal="job_succeeded", observed_at=t, value=v, limit_value=20, deadline_at=slow.deadline))
            db.add(self.O(namespace=self.NS, workload_kind="CronJob", workload="deadline-job", object_name="slow",
                          event_type="DeadlineExceeded", occurred_at=slow.outcome_at))
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.S).filter(self.S.namespace == self.NS).delete()
            db.query(self.O).filter(self.O.namespace == self.NS).delete()
            db.commit()
        finally:
            db.close()

    def test_loaders(self):
        [life] = load_storage_lives(self.NS)
        self.assertEqual((life.deployment, life.end_reason), ("disk-leak", "Evicted"))
        [job] = load_job_lives(self.NS)
        self.assertEqual((job.case, job.completions), ("positive", 20.0))

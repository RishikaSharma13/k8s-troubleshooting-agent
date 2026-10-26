"""Tests for the memory forecaster, using synthetic series with known answers."""
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.predictor.memory_forecast import (
    MiB, check_quality, first_crossing, forecast_series, load_series, samples_since_restart,
)

T0 = datetime(2026, 10, 19, 9, 0, 0, tzinfo=timezone.utc)
LIMIT = 256 * MiB


def ramp(start_mib=30.0, mib_per_min=12.0, n=40, step_s=15, noise_mib=0.0, seed=0):
    rng = np.random.default_rng(seed)
    times = [T0 + timedelta(seconds=i * step_s) for i in range(n)]
    values = [int((start_mib + mib_per_min * i * step_s / 60 + rng.normal(0, noise_mib)) * MiB) for i in range(n)]
    return times, values


def true_eta_s(start_mib, mib_per_min, n, step_s):
    last = start_mib + mib_per_min * (n - 1) * step_s / 60
    return (256 - last) / mib_per_min * 60


class TestHelpers(unittest.TestCase):
    def test_first_crossing_interpolates(self):
        x = np.array([15.0, 30.0, 45.0])
        self.assertAlmostEqual(first_crossing(x, np.array([250.0, 254.0, 258.0]), 256.0), 37.5)
        self.assertIsNone(first_crossing(x, np.array([1.0, 2.0, 3.0]), 256.0))

    def test_quality_guard(self):
        times, _ = ramp(n=5)
        self.assertIn("only 5 samples", check_quality(times))
        times, _ = ramp(n=20)
        gappy = times[:10] + [t + timedelta(seconds=300) for t in times[10:]]
        self.assertIn("gap", check_quality(gappy))
        self.assertIsNone(check_quality(times))

    def test_samples_since_restart(self):
        samples = [(T0 + timedelta(seconds=i * 15), i) for i in range(6)]
        kept = samples_since_restart(samples, T0 + timedelta(seconds=40))
        self.assertEqual([v for _, v in kept], [3, 4, 5])
        self.assertEqual(len(samples_since_restart(samples, None)), 6)


class TestForecastAccuracyOnKnownRamps(unittest.TestCase):
    """A steady leak: 12 MiB/min from 30 MiB, 10 minutes of samples, limit 256 MiB."""

    def test_linear_recovers_time_to_limit(self):
        times, values = ramp()
        r = forecast_series(times, values, LIMIT, method="linear")
        expected = true_eta_s(30, 12, 40, 15)
        self.assertEqual(r.status, "crossing_predicted")
        self.assertAlmostEqual(r.eta_s, expected, delta=0.02 * expected)
        self.assertAlmostEqual(r.trend_bytes_per_min / MiB, 12.0, delta=0.1)

    def test_prophet_recovers_time_to_limit_with_noise(self):
        times, values = ramp(noise_mib=1.0)
        r = forecast_series(times, values, LIMIT, method="prophet")
        expected = true_eta_s(30, 12, 40, 15)
        self.assertEqual(r.status, "crossing_predicted")
        self.assertAlmostEqual(r.eta_s, expected, delta=0.10 * expected)
        # The interval brackets the point estimate: earliest <= point <= latest
        self.assertLessEqual(r.eta_earliest_s, r.eta_s)
        if r.eta_latest_s is not None:
            self.assertGreaterEqual(r.eta_latest_s, r.eta_s)

    def test_flat_workload_predicts_no_crossing(self):
        times, values = ramp(start_mib=200, mib_per_min=0.0, noise_mib=2.0)
        for method in ("linear", "prophet"):
            with self.subTest(method=method):
                self.assertEqual(forecast_series(times, values, LIMIT, method=method).status, "no_crossing")

    def test_crossing_beyond_horizon_is_not_predicted(self):
        times, values = ramp(start_mib=30, mib_per_min=1.0)  # ~3.5 h to the limit
        r = forecast_series(times, values, LIMIT, method="linear", horizon_s=30 * 60)
        self.assertEqual(r.status, "no_crossing")


class TestRefusals(unittest.TestCase):
    def test_insufficient_data(self):
        times, values = ramp(n=5)
        r = forecast_series(times, values, LIMIT, method="prophet")
        self.assertEqual(r.status, "insufficient_data")
        self.assertIsNone(r.eta_s)

    def test_no_limit(self):
        times, values = ramp()
        self.assertEqual(forecast_series(times, values, None).status, "no_limit")

    def test_unknown_method(self):
        times, values = ramp()
        with self.assertRaises(ValueError):
            forecast_series(times, values, LIMIT, method="magic")


@pytest.mark.db
class TestLoadSeries(unittest.TestCase):
    NS = "pytest-forecast"

    def setUp(self):
        from app.models.database import ContainerTermination, MemorySample, SessionLocal
        self.Sample, self.Term, self.Session = MemorySample, ContainerTermination, SessionLocal
        self._cleanup()
        db = self.Session()
        try:
            for i in range(20):  # samples every 15 s from 09:00:00 to 09:04:45
                db.add(self.Sample(namespace=self.NS, pod="leak-abc", container="memload", deployment="leak",
                                   metric_timestamp=T0 + timedelta(seconds=15 * i), window_seconds=15.0,
                                   memory_bytes=(10 + i) * MiB, memory_limit_bytes=LIMIT))
            db.add(self.Term(namespace=self.NS, pod="leak-abc", container="memload", deployment="leak",
                             reason="OOMKilled", exit_code=137, finished_at=T0 + timedelta(seconds=100)))
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.Sample).filter(self.Sample.namespace == self.NS).delete()
            db.query(self.Term).filter(self.Term.namespace == self.NS).delete()
            db.commit()
        finally:
            db.close()

    def test_only_samples_after_last_termination(self):
        [s] = load_series(self.NS, "leak", at=T0 + timedelta(minutes=10))
        self.assertTrue(all(t > T0 + timedelta(seconds=100) for t in s.times))
        self.assertEqual(len(s.times), 13)          # samples at 105 s .. 285 s
        self.assertEqual(s.limit_bytes, LIMIT)

    def test_as_of_time_never_sees_later_data(self):
        """Backtests rely on this: a forecast 'as of' a past time must not use later samples."""
        at = T0 + timedelta(seconds=60)
        [s] = load_series(self.NS, "leak", at=at)
        self.assertTrue(all(t <= at for t in s.times))
        self.assertIsNone(s.last_termination)       # the termination at 100 s is in the future
        self.assertEqual(len(s.times), 5)           # samples at 0 .. 60 s


class TestForecastCurve(unittest.TestCase):
    def test_predicted_value_at_offset(self):
        times, values = ramp()                                  # 12 MiB/min, last value at 30 + 12*9.75 = 147 MiB
        r = forecast_series(times, values, LIMIT, method="linear")
        self.assertAlmostEqual(r.predicted_mib_at(300), 147 + 60, delta=0.5)   # 5 minutes later: +60 MiB
        self.assertIsNone(r.predicted_mib_at(10_000))           # beyond the horizon

    def test_no_curve_when_refusing(self):
        times, values = ramp(n=5)
        self.assertIsNone(forecast_series(times, values, LIMIT).predicted_mib_at(60))

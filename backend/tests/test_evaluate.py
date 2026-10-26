"""Tests for the forecast evaluation harness, on cases with known answers."""
import unittest
from datetime import datetime, timedelta, timezone

import pytest

from app.predictor.evaluate import (
    Life, OriginRow, apply_window, classify, exclusion_reason, threshold_share_all_samples, evaluate_life, load_lives, origin_indices, split_lives, summarize, value_at,
)
from app.predictor.memory_forecast import MiB

T0 = datetime(2026, 10, 19, 9, 0, 0, tzinfo=timezone.utc)
LIMIT = 256 * MiB


def ts(seconds):
    return T0 + timedelta(seconds=seconds)


def simulated_leak(mib_per_min=12.0, start_mib=4.7, oom_at_mib=253.0, step_s=15):
    """One container life like leak-steady: grows linearly, is OOMKilled just below the limit."""
    times, values, t, v = [], [], 0, start_mib
    while v < oom_at_mib:
        times.append(ts(t)); values.append(int(v * MiB))
        t += step_s; v = start_mib + mib_per_min * t / 60
    return times, values, ts(t - step_s + 5)            # killed 5 s after the last sample


class TestSplitLives(unittest.TestCase):
    def test_split_at_termination(self):
        times = [ts(15 * i) for i in range(10)]
        lives = split_lives("d", "p", "c", times, [i * MiB for i in range(10)], LIMIT, [(ts(62), "OOMKilled")])
        self.assertEqual([len(l.times) for l in lives], [5, 5])
        self.assertTrue(lives[0].ends_in_oom)
        self.assertIsNone(lives[1].end_reason)          # still running: censored

    def test_unrecorded_restart_is_detected(self):
        times = [ts(15 * i) for i in range(6)]
        values = [m * MiB for m in (100, 150, 200, 5, 10, 15)]   # drop of 195 MiB with no termination recorded
        lives = split_lives("d", "p", "c", times, values, LIMIT, [])
        self.assertEqual([l.end_reason for l in lives], ["unrecorded_restart", None])

    def test_termination_before_first_sample(self):
        lives = split_lives("d", "p", "c", [ts(60), ts(75)], [MiB, MiB], LIMIT, [(ts(0), "OOMKilled")])
        self.assertEqual(len(lives), 1)
        self.assertIsNone(lives[0].end_reason)


class TestCollectorGaps(unittest.TestCase):
    """The laptop slept: the collector was silent for hours in the middle of lives."""

    def test_gap_splits_a_leak_life_and_both_pieces_are_excluded(self):
        times = [ts(15 * i) for i in range(10)] + [ts(15 * 9 + 3600 + 15 * i) for i in range(1, 11)]
        values = [(10 + 2 * i) * MiB for i in range(20)]
        lives = split_lives("leak", "p", "c", times, values, LIMIT, [(times[-1] + timedelta(seconds=5), "OOMKilled")])
        self.assertEqual([(len(l.times), l.end_reason, l.starts_after_gap) for l in lives],
                         [(10, "collector_gap", False), (10, "OOMKilled", True)])
        self.assertEqual(set(classify(lives).values()), {"excluded"})
        self.assertEqual(exclusion_reason(lives[1]), "OOMKilled but starts after a collector gap (incomplete)")

    def test_gap_keeps_never_oomkilled_pieces_as_negatives(self):
        times = [ts(15 * i) for i in range(10)] + [ts(15 * 9 + 3600 + 15 * i) for i in range(1, 11)]
        lives = split_lives("flat", "p", "c", times, [200 * MiB] * 20, LIMIT, [])
        self.assertEqual(list(classify(lives).values()), ["negative", "negative"])

    def test_small_gaps_do_not_split(self):
        times = [ts(15 * i) for i in range(5)] + [ts(60 + 80 + 15 * i) for i in range(5)]   # one 80 s gap
        self.assertEqual(len(split_lives("leak", "p", "c", times, [MiB] * 10, LIMIT, [])), 1)


class TestEvaluationWindow(unittest.TestCase):
    """--since: failing lives must start inside the window; never-failing ones are trimmed."""

    def test_window_rule(self):
        ten = [ts(15 * i) for i in range(10)]
        before_fail = Life("leak", "p1", "c", "a", times=ten, values=[MiB] * 10, end_reason="OOMKilled")
        inside_fail = Life("leak", "p1", "c", "b", times=[ts(600 + 15 * i) for i in range(10)], values=[MiB] * 10,
                           end_reason="OOMKilled")
        flat = Life("flat", "p2", "c", "c", times=ten, values=[MiB] * 10)
        before_fail.end_time = ts(150)                                    # straddles the start (60 s)
        done_before = Life("leak", "p1", "c", "d", times=ten[:3], values=[MiB] * 3, end_time=ts(40), end_reason="OOMKilled")
        kept, excluded = apply_window([before_fail, inside_fail, flat, done_before], since=ts(60))
        self.assertEqual(excluded, 1)          # only the straddling one counts; the one entirely before is just outside
        self.assertEqual([l.life_id for l in kept], ["b", "c"])
        self.assertEqual(flat.times[0], ts(60))                           # never-failing life trimmed
        self.assertEqual(len(flat.times), 6)

    def test_no_window_changes_nothing(self):
        life = Life("leak", "p", "c", "a", times=[ts(0)], values=[MiB], end_reason="OOMKilled")
        self.assertEqual(apply_window([life], None), ([life], 0))


class TestHelpers(unittest.TestCase):
    def test_origin_indices_respect_step_and_end(self):
        times = [ts(15 * i) for i in range(12)]
        self.assertEqual(origin_indices(times, 60, None), [0, 4, 8])
        self.assertEqual(origin_indices(times, 60, ts(120)), [0, 4])   # nothing at or after the OOMKill

    def test_value_at(self):
        times, values = [ts(0), ts(15), ts(30)], [0, 15 * MiB, 30 * MiB]
        self.assertAlmostEqual(value_at(times, values, ts(20)), 20.0)
        self.assertIsNone(value_at(times, values, ts(31)))
        self.assertIsNone(value_at([ts(0), ts(300)], [0, MiB], ts(100)))   # gap too long to interpolate

    def test_classify(self):
        eight = [ts(15 * i) for i in range(8)]
        leak_oom = Life("leak", "p1", "c", "a", times=eight, end_reason="OOMKilled")
        leak_running = Life("leak", "p1", "c", "b", times=eight)
        flat = Life("flat", "p2", "c", "c", times=eight)
        other = Life("flat", "p2", "c", "d", times=eight, end_reason="Completed")
        too_short = Life("leak", "p1", "c", "e", times=eight[:3], end_reason="OOMKilled")
        self.assertEqual(classify([leak_oom, leak_running, flat, other, too_short]),
                         {"a": "positive", "b": "excluded", "c": "negative", "d": "excluded", "e": "excluded"})


class TestAliasingCheck(unittest.TestCase):
    def test_share_over_all_samples(self):
        times = [ts(15 * i) for i in range(8)]
        values = [m * MiB for m in (200, 200, 208, 208, 200, 200, 208, 208)]   # toggles every 30 s
        flat = Life("flat", "p", "c", "f", times=times, values=values, limit_bytes=LIMIT)
        shares = threshold_share_all_samples([flat], {"f": "negative"}, [80, 90])
        self.assertAlmostEqual(shares[("flat", 80)], 0.5)    # 208 >= 204.8, 200 < 204.8
        self.assertAlmostEqual(shares[("flat", 90)], 0.0)


class TestSummarize(unittest.TestCase):
    def _row(self, life, minutes_to_oom, method="m", alert=False, eta_min=None, earliest=None, latest=None,
             case="positive", dep="leak"):
        return OriginRow(dep, "p", life, case, ts(-minutes_to_oom * 60 if minutes_to_oom else 0), method, alert, "x",
                         100.0, 256.0, None if case == "negative" else minutes_to_oom * 60,
                         eta_s=None if eta_min is None else eta_min * 60,
                         eta_earliest_s=None if earliest is None else earliest * 60,
                         eta_latest_s=None if latest is None else latest * 60)

    def test_lead_time_misses_bias_and_coverage(self):
        rows = [
            self._row("L1", 12, alert=False), self._row("L1", 10, alert=True, eta_min=11, earliest=10.5, latest=11.5),
            self._row("L1", 5, alert=True, eta_min=5.5, earliest=4, latest=6),
            self._row("L2", 10, alert=False, eta_min=40, earliest=35, latest=45),
            self._row("L2", 5, alert=False),   # never alerted: missed
        ]
        s = summarize(rows, horizon_s=1800)[("leak", "m")]
        self.assertEqual((s["oom_lives"], s["missed"]), (2, 1))
        self.assertAlmostEqual(s["lead_min_median"], 10.0)
        self.assertAlmostEqual(s["cp600_bias_min"], (1.0 + 30.0) / 2)   # L1 +1 min late, L2 +30 min late
        self.assertEqual(s["cp600_coverage"], 0.0)                        # 10 not in [10.5, 11.5]; 10 not in L2's
        self.assertEqual(s["cp300_n"], 1)
        self.assertEqual(s["cp300_coverage"], 1.0)                        # 5 in [4, 6]
        self.assertEqual(s["cp300_no_estimate"], 1)

    def test_false_alarm_rate_and_episodes(self):
        alerts = [True, True, False, True, False, False]
        rows = [self._row("F1", 0, alert=a, case="negative", dep="flat") for a in alerts]
        for i, r in enumerate(rows):
            r.origin = ts(60 * i)
        s = summarize(rows, horizon_s=1800)[("flat", "m")]
        self.assertAlmostEqual(s["false_alarm_rate"], 0.5)
        self.assertEqual(s["false_alarm_episodes"], 2)


class TestEvaluateLife(unittest.TestCase):
    """End to end on one simulated leak life (12 MiB/min, OOMKilled at ~253 MiB)."""

    def setUp(self):
        times, values, oom = simulated_leak()
        self.life = Life("leak-steady", "p", "c", "p/c#1", times, values, LIMIT, oom, "OOMKilled")
        self.rows = evaluate_life(self.life, "positive", step_s=60, horizon_s=1800, lookback_s=900,
                                  methods=["linear"], thresholds=[80, 90])
        self.s = summarize(self.rows, horizon_s=1800)

    def test_threshold_lead_time_matches_arithmetic(self):
        # 80% of 256 = 204.8 MiB; OOMKilled at ~253 MiB; 12 MiB/min -> about 4 minutes of warning
        self.assertAlmostEqual(self.s[("leak-steady", "threshold80")]["lead_min_median"], (253 - 204.8) / 12, delta=1.0)
        self.assertAlmostEqual(self.s[("leak-steady", "threshold90")]["lead_min_median"], (253 - 230.4) / 12, delta=1.0)

    def test_forecast_warns_much_earlier_than_thresholds(self):
        lead = self.s[("leak-steady", "linear")]["lead_min_median"]
        self.assertGreater(lead, 15)                                   # alerts ~2 min into a ~21 min life
        self.assertEqual(self.s[("leak-steady", "linear")]["missed"], 0)

    def test_forecast_is_late_when_kill_happens_below_limit(self):
        # The workload dies at 253 MiB but forecasts aim at 256 MiB: they must come out late (bias > 0)
        self.assertGreater(self.s[("leak-steady", "linear")]["cp600_bias_min"], 0)

    def test_no_future_data_leaks_into_decisions(self):
        for r in self.rows:
            if r.method == "linear" and r.status != "insufficient_data":
                self.assertLessEqual(r.value_mib, 253.0)
        self.assertTrue(all(r.origin < self.life.end_time for r in self.rows))

    def test_persistence_beaten_on_a_ramp(self):
        self.assertLess(self.s[("leak-steady", "linear")]["mae_5min_mib"],
                        self.s[("leak-steady", "persistence")]["mae_5min_mib"])


@pytest.mark.db
class TestLoadLives(unittest.TestCase):
    NS = "pytest-evaluate"

    def setUp(self):
        from app.models.database import ContainerTermination, MemorySample, SessionLocal
        self.Sample, self.Term, self.Session = MemorySample, ContainerTermination, SessionLocal
        self._cleanup()
        times, values, oom = simulated_leak()
        db = self.Session()
        try:
            for t, v in zip(times, values):
                db.add(self.Sample(namespace=self.NS, pod="leak-x", container="c", deployment="leak",
                                   metric_timestamp=t, memory_bytes=v, memory_limit_bytes=LIMIT))
            db.add(self.Term(namespace=self.NS, pod="leak-x", container="c", deployment="leak",
                             reason="OOMKilled", exit_code=137, finished_at=oom))
            for i in range(20):
                db.add(self.Sample(namespace=self.NS, pod="flat-y", container="c", deployment="flat",
                                   metric_timestamp=ts(15 * i), memory_bytes=200 * MiB, memory_limit_bytes=LIMIT))
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

    def test_lives_and_cases_from_database(self):
        lives = load_lives(self.NS, None, None)
        cases = classify(lives)
        self.assertEqual(sorted(cases[l.life_id] + ":" + l.deployment for l in lives),
                         ["negative:flat", "positive:leak"])

"""Tests for conformal calibration of time-to-limit ranges (files only, no database)."""
import csv
import random
from pathlib import Path

import pytest

from app.predictor import calibrate as c


def _rows(n, seed, spread=lambda eta: 5 + eta / 30, raw_halfwidth=10.0):
    """Forecasts whose error grows with the horizon; the 'original' ranges are deliberately too narrow."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        eta = rng.uniform(30, 1790)
        actual = eta + rng.gauss(0, spread(eta))
        out.append(c.Row(eta, actual, eta - raw_halfwidth, eta + raw_halfwidth))
    return out


def test_conformal_bounds_are_the_corrected_quantiles():
    assert c.conformal_bounds(list(range(1, 100))) == (10, 90)          # n=99: 10th and 90th smallest


def test_calibrated_ranges_reach_the_target_on_unseen_data():
    cal = c.fit(_rows(3000, seed=1))
    res = c.evaluate(_rows(3000, seed=2), cal)                          # same kind of errors, new draws
    assert 0.76 <= res["all"]["cal_coverage"] <= 0.86, res["all"]
    assert res["all"]["raw_coverage"] < 0.6                             # the narrow originals fall short
    near, far = res[0]["cal_width_s"], res[3]["cal_width_s"]
    assert far > 3 * near                                               # wider when forecasting further ahead


def test_small_groups_use_all_errors_together():
    rows = [r for r in _rows(600, seed=3) if c.bucket(r.eta_s) != 3]   # no forecasts 20-30 min ahead at all
    cal = c.fit(rows)
    assert cal[3][3] is True and cal[3][2] == len(rows)                # that group falls back to all errors
    assert cal[0][3] is False                                          # a well-populated group keeps its own


def test_too_little_calibration_data_is_refused():
    with pytest.raises(ValueError):
        c.fit(_rows(10, seed=5))


def test_reads_an_evaluation_file_and_writes_a_report(tmp_path):
    fields = ["method", "case", "eta_s", "time_to_oom_s", "eta_earliest_s", "eta_latest_s"]
    def write(p, rows):
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
            for r in rows:
                w.writerow({"method": "linear", "case": "positive", "eta_s": r.eta_s, "time_to_oom_s": r.actual_s,
                            "eta_earliest_s": r.earliest_s, "eta_latest_s": r.latest_s})
            w.writerow({"method": "prophet", "case": "positive", "eta_s": 100, "time_to_oom_s": 100})   # other method
            w.writerow({"method": "linear", "case": "negative", "eta_s": 100, "time_to_oom_s": ""})     # not a crash
            w.writerow({"method": "linear", "case": "positive", "eta_s": "", "time_to_oom_s": 300})     # no forecast
    write(tmp_path / "cal.csv", _rows(400, seed=6))
    write(tmp_path / "test.csv", _rows(400, seed=7))
    assert len(c.load(tmp_path / "cal.csv", "linear")) == 400
    out = tmp_path / "report.md"
    assert c.main(["--calibration", str(tmp_path / "cal.csv"), "--test", str(tmp_path / "test.csv"), "--out", str(out)]) == 0
    text = out.read_text()
    assert "Calibrated time-to-limit ranges" in text and "| **all** |" in text


def test_corrections_round_trip_and_ranges(tmp_path, monkeypatch):
    rows = _rows(2000, seed=8)
    corr_path = tmp_path / "corr.json"
    cal_csv, test_csv = tmp_path / "cal.csv", tmp_path / "test.csv"
    for p, rs in ((cal_csv, rows), (test_csv, _rows(500, seed=9))):
        with open(p, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["method", "case", "eta_s", "time_to_oom_s", "eta_earliest_s", "eta_latest_s"])
            for r in rs:
                w.writerow(["linear", "positive", r.eta_s, r.actual_s, r.earliest_s, r.latest_s])
    assert c.main(["--calibration", str(cal_csv), "--test", str(test_csv), "--save", str(corr_path),
                   "--calibrated-on", "21-26 Oct"]) == 0
    corr = c.load_corrections(corr_path)
    assert corr["method"] == "linear" and corr["calibrated_on"] == "21-26 Oct" and 0.7 < corr["test_coverage"] < 0.9
    near, far = c.calibrated_range_s(60, corr), c.calibrated_range_s(1500, corr)
    assert near[0] < 60 < near[1] and (far[1] - far[0]) > (near[1] - near[0])
    assert c.calibrated_range_s(1, {"buckets_s": [0, 300], "bounds_s": [[-50, 5]], "method": "linear"})[0] == 0.0
    monkeypatch.setenv("CALIBRATION_FILE", str(corr_path))
    assert c.load_corrections() is not None                            # the live system finds it via the setting


def test_missing_or_malformed_corrections_mean_no_range(tmp_path):
    assert c.load_corrections(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text('{"method": "linear", "buckets_s": [0, 300], "bounds_s": []}')
    assert c.load_corrections(tmp_path / "bad.json") is None

"""Conformal calibration of the forecast's time-to-limit ranges, measured on unseen data.

The forecast's own "80%" ranges are overconfident: the true time fell inside them far less than 80%
of the time. Split conformal prediction fixes this with the forecast's past errors:

1. Calibration set (an earlier evaluation's decisions.csv): for every forecast of a crash that really
   happened, the error is   true time to the crash - forecast time to the crash.
2. Errors grow with how far ahead the forecast looks, so they are grouped by the forecast's horizon
   (BUCKETS_S). Each group's range is [forecast + q_lo, forecast + q_hi], with q_lo and q_hi the
   conformal quantiles of its errors: the ceil((n+1)*0.9)-th smallest and the floor((n+1)*0.1)-th
   smallest, which gives at least 80% coverage when future errors resemble past ones.
3. Test set (a later evaluation's decisions.csv, never used above): coverage and width of the
   original ranges versus the calibrated ones.

Decided before looking at any results: 80% level, horizon groups 0-5, 5-10, 10-20, 20-30 min,
at least MIN_N errors per group (otherwise the group uses all errors together).

Usage:
    PYTHONPATH=backend python -m app.predictor.calibrate --calibration <dir>/decisions.csv \\
        --test <dir>/decisions.csv --method linear --out calibration.md
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

LEVEL = 0.80
BUCKETS_S = (0, 300, 600, 1200, 1800)
MIN_N = 30


DEFAULT_CORRECTIONS = Path("docs/results/calibration-memory.json")   # read by the dashboard and the watcher


@dataclass
class Row:
    eta_s: float
    actual_s: float
    earliest_s: Optional[float]
    latest_s: Optional[float]


def _f(v: str) -> Optional[float]:
    return float(v) if v not in ("", "None", None) else None


def load(path: Path, method: str) -> list[Row]:
    """Forecasts of crashes that really happened, where the forecast gave a time to the limit."""
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("method") != method or r.get("case") != "positive":
                continue
            eta, actual = _f(r.get("eta_s")), _f(r.get("time_to_oom_s"))
            if eta is None or actual is None:
                continue
            rows.append(Row(eta, actual, _f(r.get("eta_earliest_s")), _f(r.get("eta_latest_s"))))
    return rows


def bucket(eta_s: float) -> int:
    for i in range(len(BUCKETS_S) - 1):
        if eta_s < BUCKETS_S[i + 1]:
            return i
    return len(BUCKETS_S) - 2


def label(i: int) -> str:
    return f"{BUCKETS_S[i] // 60}-{BUCKETS_S[i + 1] // 60} min"


def conformal_bounds(errors: Sequence[float], level: float = LEVEL) -> tuple[float, float]:
    """Split-conformal lower and upper error quantiles (finite-sample corrected, rounded outwards)."""
    e = sorted(errors)
    n = len(e)
    alpha = (1 - level) / 2
    # round first: in floating point (n+1)*0.1 can come out as 9.999... and shift the rank by one
    k_lo = max(1, math.floor(round((n + 1) * alpha, 9)))          # k-th smallest, 1-based
    k_hi = min(n, math.ceil(round((n + 1) * (1 - alpha), 9)))
    return e[k_lo - 1], e[k_hi - 1]


def fit(rows: Sequence[Row]) -> dict:
    """{bucket: (q_lo, q_hi, n_used, pooled)} from the calibration rows."""
    all_err = [r.actual_s - r.eta_s for r in rows]
    if len(all_err) < MIN_N:
        raise ValueError(f"only {len(all_err)} calibration errors; need at least {MIN_N}")
    pooled = conformal_bounds(all_err)
    out = {}
    for b in range(len(BUCKETS_S) - 1):
        err = [r.actual_s - r.eta_s for r in rows if bucket(r.eta_s) == b]
        out[b] = (*conformal_bounds(err), len(err), False) if len(err) >= MIN_N else (*pooled, len(all_err), True)
    return out


def evaluate(rows: Sequence[Row], cal: dict) -> dict:
    """Coverage and median width of the original and the calibrated ranges, per bucket and overall."""
    groups: dict = {b: [] for b in cal}
    for r in rows:
        groups[bucket(r.eta_s)].append(r)
    out = {}
    for key, rs in list(groups.items()) + [("all", list(rows))]:
        if not rs:
            continue
        raw_in = raw_w = 0
        widths_raw, widths_cal, cal_in = [], [], 0
        for r in rs:
            lo = r.earliest_s if r.earliest_s is not None else 0.0
            hi = r.latest_s if r.latest_s is not None else math.inf
            raw_in += lo <= r.actual_s <= hi
            if r.latest_s is not None and r.earliest_s is not None:
                widths_raw.append(r.latest_s - r.earliest_s)
            q_lo, q_hi, _, _ = cal[bucket(r.eta_s)]
            cal_in += r.eta_s + q_lo <= r.actual_s <= r.eta_s + q_hi
            widths_cal.append(q_hi - q_lo)
        out[key] = {"n": len(rs), "raw_coverage": raw_in / len(rs), "cal_coverage": cal_in / len(rs),
                    "raw_width_s": statistics.median(widths_raw) if widths_raw else None,
                    "cal_width_s": statistics.median(widths_cal)}
    return out


def save_corrections(path: Path, cal: dict, method: str, calibrated_on: str, test: dict) -> None:
    """The corrections the live system uses, with their provenance and measured coverage."""
    path.write_text(json.dumps({
        "method": method, "level": LEVEL, "buckets_s": list(BUCKETS_S),
        "bounds_s": [[cal[b][0], cal[b][1]] for b in sorted(cal)],
        "calibrated_on": calibrated_on,
        "test_coverage": round(test["all"]["cal_coverage"], 3) if "all" in test else None,
        "original_coverage": round(test["all"]["raw_coverage"], 3) if "all" in test else None,
    }, indent=2) + "\n")


def load_corrections(path: Optional[Path] = None) -> Optional[dict]:
    """Corrections from CALIBRATION_FILE or the default file; None if missing or malformed (then no range is shown)."""
    p = Path(path or os.environ.get("CALIBRATION_FILE") or DEFAULT_CORRECTIONS)
    try:
        c = json.loads(p.read_text())
        assert len(c["bounds_s"]) == len(c["buckets_s"]) - 1 and c["method"]
        return c
    except Exception:
        return None


def calibrated_range_s(eta_s: float, corr: dict) -> tuple[float, float]:
    """The calibrated range (seconds from now) around a forecast of eta_s seconds."""
    edges = corr["buckets_s"]
    i = next((k for k in range(len(edges) - 1) if eta_s < edges[k + 1]), len(edges) - 2)
    lo, hi = corr["bounds_s"][i]
    return max(0.0, eta_s + lo), eta_s + hi


def report(cal: dict, test: dict, method: str, cal_path: str, test_path: str, n_cal: int) -> str:
    pct = lambda v: f"{100 * v:.0f}%"
    sec = lambda v: "-" if v is None else f"{v:.0f}"
    L = [f"# Calibrated time-to-limit ranges ({method}), target {pct(LEVEL)}", "",
         f"Calibration: `{cal_path}` ({n_cal} forecasts of real crashes). Test (unseen): `{test_path}`.", "",
         "## Corrections learned from the calibration set (error = true time - forecast time)", "",
         "| Forecast horizon | Errors used | Range around the forecast (s) |", "|---|---|---|"]
    for b, (lo, hi, n, pooled) in cal.items():
        L.append(f"| {label(b)} | {n}{' (all horizons pooled)' if pooled else ''} | {lo:+.0f} to {hi:+.0f} |")
    L += ["", "## On the test set", "",
          "| Forecast horizon | Forecasts | Original range: coverage | median width (s) | "
          "Calibrated: coverage | median width (s) |", "|---|---|---|---|---|---|"]
    for key, s in test.items():
        name = "**all**" if key == "all" else label(key)
        L.append(f"| {name} | {s['n']} | {pct(s['raw_coverage'])} | {sec(s['raw_width_s'])} | "
                 f"{pct(s['cal_coverage'])} | {sec(s['cal_width_s'])} |")
    L += ["", f"Coverage = share of forecasts whose true time to the crash fell inside the range (target {pct(LEVEL)}).",
          "The guarantee assumes future errors resemble past ones; the test set checks whether they did."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Conformal calibration of time-to-limit ranges.")
    ap.add_argument("--calibration", required=True, type=Path, help="decisions.csv of an earlier evaluation")
    ap.add_argument("--test", required=True, type=Path, help="decisions.csv of a later evaluation (unseen)")
    ap.add_argument("--method", default="linear")
    ap.add_argument("--out", type=Path, help="write the report here (default: print it)")
    ap.add_argument("--save", type=Path, help="also write the corrections for the live system (JSON)")
    ap.add_argument("--calibrated-on", default="", help="label for the calibration window, stored with --save")
    args = ap.parse_args(argv)
    cal_rows = load(args.calibration, args.method)
    cal = fit(cal_rows)
    test = evaluate(load(args.test, args.method), cal)
    text = report(cal, test, args.method, str(args.calibration), str(args.test), len(cal_rows))
    if args.save:
        save_corrections(args.save, cal, args.method, args.calibrated_on or str(args.calibration), test)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

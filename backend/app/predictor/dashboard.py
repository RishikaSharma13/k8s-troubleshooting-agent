"""Memory-prediction dashboard: one self-contained HTML page (READ-ONLY).

Reads memory_samples / container_terminations, runs the live forecast, and
draws the evaluation results. No server: open the HTML file in a browser.

    PYTHONPATH=backend python -m app.predictor.dashboard --out dashboard.html \
        --report docs/results/evaluation-2026-10-21.md [--signals-report <evaluate_signals report.md>]

Sections: memory (live forecast, a checked prediction, evaluation), storage
eviction, and Job deadlines.
"""
import argparse
import base64
import html
import io
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from app.predictor.evaluate import classify, load_lives
from app.predictor.evaluate_signals import (JOB_QUALITY, STORAGE_HORIZON_S, STORAGE_LOOKBACK_S, STORAGE_MIN_POINTS,
                                           STORAGE_QUALITY)
from app.predictor.memory_forecast import MiB, forecast_deployment, forecast_series

logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
IST = ZoneInfo("Asia/Kolkata")
NS = "predict-lab"
DEPLOYMENTS = ("leak-steady", "leak-slow", "flat-hot")
COLORS = {"leak-steady": "#d62728", "leak-slow": "#ff7f0e", "flat-hot": "#1f77b4"}


def png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def chart_recent(lives, minutes=90):
    """Memory of each workload over the last N minutes, with limit, 80% line and OOMKills."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes)
    fig, ax = plt.subplots(figsize=(11, 4.2))
    for dep in DEPLOYMENTS:
        first = True
        for l in (l for l in lives if l.deployment == dep):
            pts = [(t, v / MiB) for t, v in zip(l.times, l.values) if t >= start]
            if pts:
                ax.plot([p[0] for p in pts], [p[1] for p in pts], color=COLORS[dep], lw=1.6,
                        label=dep if first else None)
                first = False
            if l.ends_in_oom and l.end_time and l.end_time >= start:
                ax.axvline(l.end_time, color=COLORS[dep], ls=":", lw=1)
    ax.axhline(256, color="black", lw=1.2); ax.text(start, 252, " limit 256 MiB", fontsize=8, va="top")
    ax.axhline(0.8 * 256, color="grey", ls="--", lw=1); ax.text(start, 0.8 * 256 - 4, " 80% threshold alert", fontsize=8, va="top", color="grey")
    ax.set_ylim(0, 300); ax.set_ylabel("memory (MiB)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=IST)); ax.set_xlabel("time (IST)")
    ax.plot([], [], color="grey", ls=":", lw=1, label="OOMKilled")
    ax.legend(loc="upper left", ncol=4, fontsize=8); ax.grid(alpha=0.25)
    ax.set_title(f"Live: memory of the three workloads, last {minutes} minutes")
    return png(fig)


def chart_prediction_example(lives):
    """The latest complete leak-steady life: what the forecast said 10 minutes before the OOMKill."""
    cases = classify(lives)
    cands = [l for l in lives if l.deployment == "leak-steady" and cases[l.life_id] == "positive"]
    if not cands:
        return None, None
    life = max(cands, key=lambda l: l.end_time)
    origin_i = min(range(len(life.times)), key=lambda i: abs((life.end_time - life.times[i]).total_seconds() - 600))
    t_o = life.times[origin_i]
    w = [i for i in range(origin_i + 1) if (t_o - life.times[i]).total_seconds() < 900]
    r = forecast_series([life.times[i] for i in w], [life.values[i] for i in w], life.limit_bytes, method="linear")
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(life.times, [v / MiB for v in life.values], color="#d62728", lw=1.8, label="actual memory (leak-steady)")
    ax.plot([t_o + timedelta(seconds=s) for s in r.future_offsets_s], r.future_point_mib, color="black", ls="--", lw=1.4,
            label="forecast made 10 min before the kill (linear)")
    ax.axvline(t_o, color="grey", lw=1); ax.text(t_o, 20, " forecast made", fontsize=8, color="grey")
    ax.axhline(256, color="black", lw=1.2); ax.text(life.times[0], 252, " limit 256 MiB", fontsize=8, va="top")
    note = ""
    if r.eta_s is not None:
        predicted = t_o + timedelta(seconds=r.eta_s)
        if r.eta_earliest_s is not None:
            hi = t_o + timedelta(seconds=r.eta_latest_s) if r.eta_latest_s is not None else predicted
            ax.axvspan(t_o + timedelta(seconds=r.eta_earliest_s), hi, color="black", alpha=0.08, label="80% interval")
        ax.axvline(predicted, color="black", ls="--", lw=1)
        err = (predicted - life.end_time).total_seconds()
        note = (f"Predicted kill {predicted.astimezone(IST):%H:%M:%S}, actual {life.end_time.astimezone(IST):%H:%M:%S} "
                f"IST: error {err:+.0f} s, predicted {r.eta_s / 60:.1f} min ahead.")
    ax.axvline(life.end_time, color="#d62728", ls=":", lw=1.5, label="actual OOMKill")
    ax.set_ylim(0, 300); ax.set_xlim(life.times[0], life.end_time + timedelta(minutes=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=IST)); ax.set_xlabel("time (IST)")
    ax.set_ylabel("memory (MiB)"); ax.legend(loc="lower right", fontsize=8); ax.grid(alpha=0.25)
    ax.set_title(f"A real prediction, checked against reality ({life.end_time.astimezone(IST):%d %b})")
    return png(fig), note


def parse_report(path):
    """{(deployment, method): row dict} from the evaluation report's Markdown tables."""
    rows, dep, header = {}, None, None
    for line in open(path):
        if line.startswith("## "):
            dep, header = line[3:].strip(), None
        elif line.startswith("| Method"):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
        elif header and line.startswith("| ") and not line.startswith("|---"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows[(dep, cells[0])] = dict(zip(header, cells))
    return rows


def memory_report_facts(path, rows):
    """Numbers quoted in section 3, read from the memory report itself (never hard-coded)."""
    leaks = ("leak-steady", "leak-slow")
    n_oom = sum(int(rows.get((d, "linear"), {}).get("OOM lives", "0") or 0) for d in leaks)
    cov = []
    for d in leaks:
        for m in ("linear", "prophet"):
            for col in ("Coverage @10m", "Coverage @5m"):
                v = rows.get((d, m), {}).get(col, "-")
                if v.endswith("%"):
                    cov.append(int(v[:-1]))
    window = ""
    for line in open(path):
        if line.startswith("Generated") and "Parameters:" in line:
            params = dict(kv.split("=", 1) for kv in line.split("Parameters:", 1)[1].strip().split(", ") if "=" in kv)
            since, until = params.get("since", "None"), params.get("until", "None")
            window = (f"data from {since} to {until} UTC" if since != "None" else f"data up to {until} UTC")
            break
    coverage = f"{min(cov)}-{max(cov)}%" if cov else "well under 80%"
    return {"n_oom": n_oom, "coverage": coverage, "window": window}


def chart_evaluation(rows):
    num = lambda s: float(s.rstrip("%")) if s not in ("-", "") else 0.0
    methods = ["linear", "prophet", "threshold80", "threshold90"]
    colors = ["#2ca02c", "#9467bd", "#7f7f7f", "#bcbd22"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.8), gridspec_kw={"width_ratios": [2, 1]})
    for k, (m, c) in enumerate(zip(methods, colors)):
        vals = [num(rows.get((d, m), {}).get("Lead time median (min)", "-")) for d in ("leak-steady", "leak-slow")]
        bars = a1.bar([x + k * 0.2 for x in range(2)], vals, 0.2, color=c, label=m)
        a1.bar_label(bars, fmt="%.1f", fontsize=8)
        fa = num(rows.get(("flat-hot", m), {}).get("False-alarm rate", "-"))
        bar = a2.bar(k, fa, color=c); a2.bar_label(bar, fmt="%.0f%%", fontsize=8)
    a1.set_xticks([x + 0.3 for x in range(2)], ["leak-steady", "leak-slow"]); a1.set_ylabel("minutes of warning (median)")
    a1.set_title("Warning before OOMKill (higher is better)"); a1.legend(fontsize=8); a1.grid(axis="y", alpha=0.25)
    a2.set_xticks(range(4), methods, rotation=20, fontsize=8); a2.set_ylabel("% of decisions")
    a2.set_title("False alarms on flat-hot (lower is better)"); a2.grid(axis="y", alpha=0.25)
    return png(fig)


def live_forecasts():
    out = []
    for dep in DEPLOYMENTS:
        for method in ("linear", "prophet"):
            for _, r in forecast_deployment(NS, dep, method=method):
                if r.status == "crossing_predicted" and r.eta_s is not None:
                    at = (r.window_end + timedelta(seconds=r.eta_s)).astimezone(IST)
                    verdict = f"reaches limit in <b>{r.eta_s / 60:.1f} min</b>, at about <b>{at:%H:%M:%S}</b> IST"
                    css = "warn"
                elif r.status == "no_crossing":
                    verdict = f"not expected to reach the limit within {r.horizon_s / 60:.0f} min"; css = "ok"
                else:
                    verdict = html.escape(f"{r.status}: {r.reason}"); css = "muted"
                now = f"{r.last_bytes / MiB:.1f} MiB ({r.current_percent:.1f}%)" if r.current_percent is not None else "-"
                alert80 = "FIRING" if r.current_percent is not None and r.current_percent >= 80 else "quiet"
                out.append((dep, method, now, alert80, verdict, css))
    return out


# ------------------------------------------------------------------
# Storage eviction and Job deadlines (signal_samples / outcome_events)
# ------------------------------------------------------------------
def _signal_rows(signal, since):
    from app.models.database import OutcomeEvent, SessionLocal, SignalSample
    db = SessionLocal()
    try:
        rows = (db.query(SignalSample.workload, SignalSample.object_name, SignalSample.observed_at, SignalSample.value,
                         SignalSample.limit_value, SignalSample.deadline_at)
                .filter(SignalSample.namespace == NS, SignalSample.signal == signal, SignalSample.observed_at >= since)
                .order_by(SignalSample.observed_at).all())
        events = (db.query(OutcomeEvent.workload, OutcomeEvent.object_name, OutcomeEvent.event_type, OutcomeEvent.occurred_at)
                  .filter(OutcomeEvent.namespace == NS, OutcomeEvent.occurred_at >= since).all())
    finally:
        db.close()
    return rows, events


def storage_section(minutes=120):
    now = datetime.now(timezone.utc)
    rows, events = _signal_rows("ephemeral_storage_bytes", now - timedelta(minutes=minutes))
    if not rows:
        return None, None
    evicted = {(o, t) for w, o, e, t in events if e == "Evicted"}
    table = []
    for dep in sorted({r[0] for r in rows}):
        latest_pod = max((r for r in rows if r[0] == dep), key=lambda r: r[2])[1]
        pod_rows = [r for r in rows if r[1] == latest_pod]
        recent = [r for r in pod_rows if r[2] >= now - timedelta(seconds=STORAGE_LOOKBACK_S)]
        series = recent if len(recent) >= STORAGE_MIN_POINTS else pod_rows[-STORAGE_MIN_POINTS:]
        if not series or latest_pod in {o for o, _ in evicted}:
            continue
        limit = series[-1][4]
        pct = 100 * series[-1][3] / limit if limit else None
        r = forecast_series([x[2] for x in series], [x[3] for x in series], int(limit) if limit else None,
                            method="linear", horizon_s=STORAGE_HORIZON_S, quality=STORAGE_QUALITY)
        if r.status == "crossing_predicted" and r.eta_s is not None:
            at = (r.window_end + timedelta(seconds=r.eta_s)).astimezone(IST)
            verdict, css = f"reaches limit in <b>{r.eta_s / 60:.1f} min</b>, at about <b>{at:%H:%M:%S}</b> IST", "warn"
        elif r.status == "no_crossing":
            verdict, css = "not expected to reach the limit within 30 min", "ok"
        else:
            verdict, css = html.escape(f"{r.status}: {r.reason}"), "muted"
        alert = "FIRING" if pct is not None and pct >= 80 else "quiet"
        table.append(f"<tr><td>{dep}</td><td>{series[-1][3] / MiB:.1f} MiB ({pct:.0f}%)</td>"
                     f"<td class='{'warn' if alert == 'FIRING' else 'ok'}'>{alert}</td><td class='{css}'>{verdict}</td></tr>")
    fig, ax = plt.subplots(figsize=(11, 3.8))
    colors = {"disk-leak": "#8c564b", "disk-flat": "#17becf"}
    labelled = set()
    for obj in sorted({r[1] for r in rows}):
        pts = [r for r in rows if r[1] == obj]
        dep = pts[0][0]
        ax.step([r[2] for r in pts], [r[3] / MiB for r in pts], where="post", color=colors.get(dep, "grey"), lw=1.6,
                label=dep if dep not in labelled else None)
        labelled.add(dep)
    for o, t in evicted:
        ax.axvline(t, color="#8c564b", ls=":", lw=1)
    ax.plot([], [], color="grey", ls=":", lw=1, label="Evicted")
    ax.axhline(100, color="black", lw=1.2); ax.axhline(80, color="grey", ls="--", lw=1)
    ax.text(now - timedelta(minutes=minutes), 101, " limit 100 MiB", fontsize=8)
    ax.text(now - timedelta(minutes=minutes), 76, " 80% threshold alert", fontsize=8, color="grey")
    ax.set_ylim(0, 120); ax.set_ylabel("local storage (MiB)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=IST)); ax.set_xlabel("time (IST)")
    ax.legend(loc="upper left", ncol=3, fontsize=8); ax.grid(alpha=0.25)
    ax.set_title(f"Storage of the disk workloads, last {minutes} minutes (readings as the kubelet re-measures)")
    return "\n".join(table), png(fig)


def jobs_section(n_jobs=8):
    now = datetime.now(timezone.utc)
    rows, events = _signal_rows("job_succeeded", now - timedelta(hours=2))
    if not rows:
        return None, None
    outcome = {o: (e, t) for w, o, e, t in events if e in ("DeadlineExceeded", "Complete")}
    jobs = sorted({r[1] for r in rows}, key=lambda j: min(r[2] for r in rows if r[1] == j))[-n_jobs:]
    table = []
    fig, ax = plt.subplots(figsize=(11, 3.8))
    for j in jobs:
        pts = [r for r in rows if r[1] == j]
        start, deadline, total = pts[0][2], pts[-1][5], pts[-1][4]
        ev = outcome.get(j)
        if ev:
            state = "missed deadline" if ev[0] == "DeadlineExceeded" else "finished in time"
            css = "warn" if ev[0] == "DeadlineExceeded" else "ok"
            color = "#d62728" if ev[0] == "DeadlineExceeded" else "#2ca02c"
            r = forecast_series([x[2] for x in pts if (x[2] - start).total_seconds() <= 0.5 * (deadline - start).total_seconds()],
                                [x[3] for x in pts if (x[2] - start).total_seconds() <= 0.5 * (deadline - start).total_seconds()],
                                int(total), method="linear", horizon_s=600, step_s=5.0, quality=JOB_QUALITY, scale=1.0)
            if r.eta_s is not None:
                fin = r.window_end + timedelta(seconds=r.eta_s)
                said = "late" if fin > deadline else "on time"
                forecast_txt = f"at halfway, forecast said <b>{said}</b>"
            else:
                forecast_txt = f"at halfway: {r.status}"
        else:
            r = forecast_series([x[2] for x in pts], [x[3] for x in pts], int(total), method="linear",
                                horizon_s=max(60.0, (deadline - now).total_seconds() + 300), step_s=5.0,
                                quality=JOB_QUALITY, scale=1.0)
            color, state, css = "#1f77b4", "running", "muted"
            if r.eta_s is not None:
                fin = r.window_end + timedelta(seconds=r.eta_s)
                late_s = (fin - deadline).total_seconds()
                forecast_txt = (f"predicted finish {fin.astimezone(IST):%H:%M:%S}: <b>" +
                                (f"late by {late_s:.0f} s" if late_s > 0 else f"on time ({-late_s:.0f} s spare)") + "</b>")
                css = "warn" if late_s > 0 else "ok"
            else:
                forecast_txt = html.escape(r.status if r.status != "insufficient_data" else "collecting data")
        table.append(f"<tr><td>{j}</td><td>{int(pts[-1][3])}/{int(total)}</td><td>{deadline.astimezone(IST):%H:%M:%S}</td>"
                     f"<td class='{css}'>{state}</td><td>{forecast_txt}</td></tr>")
        ax.step([(x[2] - start).total_seconds() for x in pts], [x[3] for x in pts], where="post", color=color, lw=1.4, alpha=0.85)
    ax.axvline(285, color="black", lw=1.2); ax.text(287, 1, "deadline (285 s)", fontsize=8)
    ax.axvline(0.8 * 285, color="grey", ls="--", lw=1); ax.text(0.8 * 285 - 2, 1, "80% of time:\nthreshold alert", fontsize=8, color="grey", ha="right")
    for c, lbl in (("#d62728", "missed deadline"), ("#2ca02c", "finished in time"), ("#1f77b4", "running")):
        ax.plot([], [], color=c, lw=1.6, label=lbl)
    ax.set_xlim(0, 320); ax.set_ylim(0, 21); ax.set_yticks(range(0, 21, 5))
    ax.set_xlabel("seconds since the job started"); ax.set_ylabel("items completed (of 20)")
    ax.legend(loc="upper left", fontsize=8); ax.grid(alpha=0.25)
    ax.set_title(f"Progress of the last {len(jobs)} jobs against their deadline")
    return "\n".join(reversed(table)), png(fig)


def new_prediction_sections(storage_table, storage_chart, jobs_table, jobs_chart, sig_summary) -> str:
    """HTML for sections 4-6 (kept out of the page template: Python 3.10 has no nested f-strings)."""
    if storage_chart:
        storage = ("<table><tr><th>workload</th><th>storage now</th><th>80% threshold alert</th>"
                   "<th>forecast (linear)</th></tr>" + storage_table + "</table>"
                   f'<img src="data:image/png;base64,{storage_chart}" alt="local storage of the disk workloads">'
                   '<p class="note">The kubelet re-measures disk only every ~70-100 s, so this signal moves in steps '
                   'and is coarser than memory.</p>')
    else:
        storage = "<p>No storage readings yet.</p>"
    if jobs_chart:
        jobs = ("<table><tr><th>job</th><th>items</th><th>deadline (IST)</th><th>status</th><th>forecast</th></tr>"
                + jobs_table + "</table>"
                f'<img src="data:image/png;base64,{jobs_chart}" alt="progress of recent jobs against their deadline">'
                '<p class="note">A "running too long" alert (80% of the allowed time) cannot say anything before '
                '228 s; the forecast decides from the first few items.</p>')
    else:
        jobs = "<p>No job readings yet.</p>"
    out = ('<div class="card"><h2>4. Storage: when will a pod be evicted for using too much local disk?</h2>'
           + storage + "</div>\n"
           '<div class="card"><h2>5. Jobs: will this job finish before its deadline?</h2>' + jobs + "</div>\n")
    if sig_summary:
        out += ('<div class="card"><h2>6. Evaluation of the new predictions</h2>' + sig_summary +
                '<p class="note">Backtest from app.predictor.evaluate_signals; same rules as the memory evaluation.</p></div>\n')
    return out


def _report_tables(path):
    """{(section, method): {column header: value}} for every table in an evaluate_signals report."""
    out, section, header = {}, None, None
    for line in open(path):
        if line.startswith("### "):
            section, header = line[4:].strip(), None
        elif line.startswith("## Jobs"):
            section, header = "jobs", None
        elif line.startswith("| Method"):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
        elif header and line.startswith("| ") and not line.startswith("|---"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out[(section, cells[0])] = dict(zip(header, cells))
    return out


def signals_summary(path):
    """Key numbers from an evaluate_signals report (optional). Columns are read by name."""
    if not path:
        return ""
    t = _report_tables(path)
    get = lambda sec, m, col: t.get((sec, m), {}).get(col, "-")
    out = []
    if ("disk-leak", "linear") in t:
        out.append("<h3>Storage eviction</h3><table><tr><th>method</th><th>evictions</th><th>missed</th>"
                   "<th>warning (median min)</th><th>false alarms on disk-flat</th><th>no forecast on disk-flat</th></tr>" +
                   "".join(f"<tr><td>{m}</td><td>{get('disk-leak', m, 'Evicted lives')}</td>"
                           f"<td>{get('disk-leak', m, 'Missed')}</td><td>{get('disk-leak', m, 'Warning median (min)')}</td>"
                           f"<td>{get('disk-flat', m, 'False-alarm rate')}</td>"
                           f"<td>{get('disk-flat', m, 'No forecast (too little data)')}</td></tr>"
                           for m in ("linear", "prophet", "threshold80", "threshold90")) + "</table>")
    if ("jobs", "linear") in t:
        out.append("<h3>Job deadlines</h3><table><tr><th>method</th><th>late jobs</th><th>missed</th>"
                   "<th>warning (median s)</th><th>accuracy at 50% of time</th><th>at 75%</th></tr>" +
                   "".join(f"<tr><td>{m}</td><td>{get('jobs', m, 'Late jobs')}</td><td>{get('jobs', m, 'Missed')}</td>"
                           f"<td>{get('jobs', m, 'Warning before deadline (median s)')}</td>"
                           f"<td>{get('jobs', m, 'Accuracy @50% time')}</td><td>{get('jobs', m, 'Accuracy @75% time')}</td></tr>"
                           for m in ("linear", "prophet", "threshold80") if ("jobs", m) in t) + "</table>")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--refresh-s", type=int, default=60)
    ap.add_argument("--signals-report", help="report.md from app.predictor.evaluate_signals (optional)")
    args = ap.parse_args()
    lives = load_lives(NS, datetime.now(timezone.utc) - timedelta(hours=26), None)
    rows = parse_report(args.report)
    recent = chart_recent(lives)
    example, note = chart_prediction_example(lives)
    evaluation = chart_evaluation(rows)
    facts = memory_report_facts(args.report, rows)
    live = live_forecasts()
    storage_table, storage_chart = storage_section()
    jobs_table, jobs_chart = jobs_section()
    sig_summary = signals_summary(args.signals_report)
    extra_sections = new_prediction_sections(storage_table, storage_chart, jobs_table, jobs_chart, sig_summary)
    gen = datetime.now(timezone.utc).astimezone(IST)
    table = "\n".join(
        f"<tr><td>{d}</td><td>{m}</td><td>{n}</td><td class='{'warn' if a == 'FIRING' else 'ok'}'>{a}</td>"
        f"<td class='{c}'>{v}</td></tr>" for d, m, n, a, v, c in live)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{args.refresh_s}"><title>Failure prediction - predict-lab</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 24px auto; max-width: 1180px; color: #222; }}
 h1 {{ margin-bottom: 0 }} .sub {{ color: #666; margin-top: 4px }}
 table {{ border-collapse: collapse; width: 100%; margin: 8px 0 18px }} td, th {{ border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: left; font-size: 14px }}
 .warn {{ color: #b00020; font-weight: 600 }} .ok {{ color: #1b7f3b }} .muted {{ color: #888 }}
 .card {{ border: 1px solid #e3e3e3; border-radius: 8px; padding: 12px 16px; margin: 14px 0 }}
 img {{ max-width: 100% }} .note {{ background: #f6f6f6; padding: 8px 12px; border-radius: 6px }}
 .limits li {{ margin: 4px 0 }}
</style></head><body>
<h1>Predicting failures before they happen</h1>
<div class="sub">namespace <code>predict-lab</code> - generated {gen:%d %b %Y %H:%M:%S} IST - refreshes every {args.refresh_s} s - read-only</div>

<div class="card"><h2>1. Live forecast: when will each container hit its memory limit?</h2>
<table><tr><th>workload</th><th>method</th><th>memory now</th><th>80% threshold alert</th><th>forecast</th></tr>
{table}</table>
<img src="data:image/png;base64,{recent}" alt="memory of the three workloads, last 90 minutes"></div>

<div class="card"><h2>2. Does the forecast come true?</h2>
{f'<img src="data:image/png;base64,{example}" alt="forecast vs actual for one OOMKill"><p class="note">{note}</p>' if example else '<p>No complete leak-steady life in the last 26 hours.</p>'}</div>

<div class="card"><h2>3. Evaluation: forecasts vs threshold alerts ({facts["n_oom"]} real OOMKills)</h2>
<img src="data:image/png;base64,{evaluation}" alt="warning time and false alarms by method">
<p>Backtest on {facts["window"]}: each decision used only data available at that moment.</p>
<h3>Honest limitations</h3><ul class="limits">
<li>Prediction intervals are overconfident: actual times fell inside the 80% interval only {facts["coverage"]} of the time.</li>
<li>Prophet performed no better than linear regression: the test leaks are linear by design.</li>
<li>Controlled workloads in one kind cluster: this demonstrates the method, not general accuracy.</li></ul></div>

{extra_sections}
</body></html>"""
    open(args.out, "w").write(page)
    print(f"wrote {args.out} ({len(page) // 1024} KiB) at {gen:%H:%M:%S} IST")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # keep the demo calm: one clear line instead of a traceback
        from sqlalchemy.exc import OperationalError
        if isinstance(exc, OperationalError):
            raise SystemExit("Database not reachable. Check: docker exec k8s-agent-db pg_isready -U postgres")
        raise

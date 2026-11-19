"""Prometheus exporter for the failure forecasts: read-only, metrics only.

A separate process from the API on purpose: the API has approve/execute endpoints and listens on
127.0.0.1 only. Prometheus runs inside the cluster, so it needs an address the cluster can reach;
this exporter serves nothing but GET /metrics and GET /health, and is meant to listen on the Docker
network between the laptop and the kind cluster (not on the LAN).

Metrics (computed from the same database the dashboard uses, linear forecasts):
  k8s_failure_seconds_to_limit{namespace,deployment,pod}         seconds until the forecast limit crossing
  k8s_failure_seconds_to_limit_lower / _upper{...}               calibrated 80% range (if calibrated)
  k8s_failure_memory_usage_ratio{...}                             last reading / limit (what an 80% alert uses)
  k8s_failure_forecast_status{...,status}                         1 for the current forecast status
  k8s_failure_incidents{outcome}                                  forecast incidents by verdict
  k8s_failure_executions{action,status}                           executions by result

Usage:
    PYTHONPATH=backend python -m app.metrics.exporter --host 172.18.0.1 --port 9105
"""
from __future__ import annotations

import argparse
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Sequence

from loguru import logger
from sqlalchemy import func

from app.models.database import Execution, Incident, MemorySample, SessionLocal
from app.predictor.calibrate import calibrated_range_s, load_corrections
from app.predictor.memory_forecast import forecast_deployment

P = "k8s_failure_"
DEFAULT_NAMESPACES = ("predict-lab", "remediate-lab", "pattern-lab")
ACTIVE_S = 600            # deployments with a reading in the last 10 minutes are exported
CACHE_S = 10.0

HELP = {
    "seconds_to_limit": "Seconds until the linear forecast reaches the memory limit (only when within the horizon).",
    "seconds_to_limit_lower": "Calibrated 80% range, earliest (seconds from now).",
    "seconds_to_limit_upper": "Calibrated 80% range, latest (seconds from now).",
    "memory_usage_ratio": "Latest working-set reading divided by the memory limit.",
    "forecast_status": "1 for the current forecast status of the container.",
    "incidents": "Forecast incidents by verification outcome (pending = not yet verified).",
    "executions": "Remediation executions by action and result.",
}


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name: str, labels: dict, value: float) -> str:
    lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
    return f"{P}{name}{{{lab}}} {value:.6g}" if lab else f"{P}{name} {value:.6g}"


def active_deployments(db, namespace: str, now: datetime) -> list[str]:
    rows = (db.query(MemorySample.deployment).filter(MemorySample.namespace == namespace,
                                                     MemorySample.metric_timestamp > now - timedelta(seconds=ACTIVE_S))
            .distinct().all())
    return sorted(d for (d,) in rows if d)


def collect(now: Optional[datetime] = None, namespaces: Sequence[str] = DEFAULT_NAMESPACES,
            session_factory: Callable = SessionLocal) -> str:
    now = now or datetime.now(timezone.utc)
    series: dict[str, list[str]] = {k: [] for k in HELP}
    corr = load_corrections()
    db = session_factory()
    try:
        for ns in namespaces:
            for dep in active_deployments(db, ns, now):
                for s, r in forecast_deployment(ns, dep, method="linear", at=now, session_factory=session_factory):
                    lab = {"namespace": ns, "deployment": dep, "pod": s.pod}
                    series["forecast_status"].append(_sample("forecast_status", {**lab, "status": r.status}, 1))
                    if s.limit_bytes and s.values:
                        series["memory_usage_ratio"].append(_sample("memory_usage_ratio", lab, s.values[-1] / s.limit_bytes))
                    if r.status == "crossing_predicted" and r.eta_s is not None and r.window_end is not None:
                        age = (now - r.window_end).total_seconds()             # the forecast's "now" is its last reading
                        series["seconds_to_limit"].append(_sample("seconds_to_limit", lab, max(0.0, r.eta_s - age)))
                        if corr and corr.get("method") == "linear":
                            lo, hi = calibrated_range_s(r.eta_s, corr)
                            series["seconds_to_limit_lower"].append(_sample("seconds_to_limit_lower", lab, max(0.0, lo - age)))
                            series["seconds_to_limit_upper"].append(_sample("seconds_to_limit_upper", lab, max(0.0, hi - age)))
        for outcome, n in (db.query(Incident.outcome, func.count()).filter(Incident.source == "forecast")
                           .group_by(Incident.outcome).all()):
            series["incidents"].append(_sample("incidents", {"outcome": outcome or "pending"}, n))
        for action, status, n in db.query(Execution.action, Execution.status, func.count()).group_by(
                Execution.action, Execution.status).all():
            series["executions"].append(_sample("executions", {"action": action, "status": status}, n))
    finally:
        db.close()
    lines = []
    for name, samples in series.items():
        lines += [f"# HELP {P}{name} {HELP[name]}", f"# TYPE {P}{name} gauge"] + samples
    return "\n".join(lines) + "\n"


class Cache:
    def __init__(self, fn: Callable[[], str], ttl: float = CACHE_S):
        self.fn, self.ttl, self.value, self.at, self.lock = fn, ttl, None, 0.0, threading.Lock()

    def get(self) -> str:
        with self.lock:
            if self.value is None or time.monotonic() - self.at > self.ttl:
                self.value, self.at = self.fn(), time.monotonic()
            return self.value


def make_server(host: str, port: int, render: Callable[[], str]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                                   # GET only: nothing here can change anything
            if self.path == "/metrics":
                try:
                    body, code = render().encode(), 200
                except Exception as exc:                    # report, do not crash the exporter
                    logger.exception("collect failed: {}", exc)
                    body, code = b"collect failed\n", 500
                ctype = "text/plain; version=0.0.4; charset=utf-8"
            elif self.path == "/health":
                body, code, ctype = b"ok\n", 200, "text/plain"
            else:
                body, code, ctype = b"not found\n", 404, "text/plain"
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prometheus exporter for failure forecasts (read-only).")
    ap.add_argument("--host", default="127.0.0.1", help="e.g. the kind Docker network gateway, so pods can reach it")
    ap.add_argument("--port", type=int, default=9105)
    ap.add_argument("--namespace", action="append", help="namespace to export (repeatable)")
    args = ap.parse_args(argv)
    namespaces = args.namespace or [n for n in os.environ.get("METRICS_NAMESPACES", "").split(",") if n] or DEFAULT_NAMESPACES
    cache = Cache(lambda: collect(namespaces=namespaces))
    server = make_server(args.host, args.port, cache.get)
    logger.info("Exporter on http://{}:{}/metrics for {}", args.host, args.port, ",".join(namespaces))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

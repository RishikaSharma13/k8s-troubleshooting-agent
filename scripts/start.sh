#!/usr/bin/env bash
# Start every background service of the lab (or check it is running). Safe to run again.
#
# Settings: optional file ~/.k8s-failure-lab.env, for example
#   DATA_DIR=$HOME/k8s-failure-lab                         # logs and the generated dashboard
#   DATABASE_URL=postgresql://postgres:password@localhost:5432/k8s_agent
#   EXPERIMENT_LOG=$HOME/k8s-failure-lab/experiment.jsonl  # shown on the dashboard if it exists
#
# Services (systemd user units): k8s-collector, k8s-watch, k8s-dashboard, k8s-api, k8s-exporter,
# and, when the monitoring namespace exists, k8s-pf-prometheus (localhost:9090) and k8s-pf-grafana (localhost:3000).
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
[ -f "$HOME/.k8s-failure-lab.env" ] && . "$HOME/.k8s-failure-lab.env"
DATA_DIR="${DATA_DIR:-$HOME/k8s-failure-lab}"
DATABASE_URL="${DATABASE_URL:-postgresql://postgres:password@localhost:5432/k8s_agent}"
EXPERIMENT_LOG="${EXPERIMENT_LOG:-$DATA_DIR/experiment.jsonl}"
NAMESPACES="${NAMESPACES:-default predict-lab remediate-lab pattern-lab}"
PY="$REPO/venv/bin/python"
KUBECTL="$(command -v kubectl)"
mkdir -p "$DATA_DIR/logs"
[ -x "$PY" ] || { echo "no virtual environment at $REPO/venv (see README: Setup)"; exit 1; }

running() { systemctl --user is-active --quiet "$1"; }
start() {   # start <unit> <log name> <command...>   (only when not already running)
  local unit="$1" log="$DATA_DIR/logs/$2.log"; shift 2
  running "$unit" && { echo "  $unit already running"; return; }
  systemctl --user reset-failed "$unit" 2>/dev/null
  systemd-run --user --quiet --unit="$unit" --working-directory="$REPO" \
    --setenv=DATABASE_URL="$DATABASE_URL" --setenv=PYTHONPATH=backend --setenv=PATH="$PATH" \
    --setenv=DASHBOARD_HTML="$DATA_DIR/dashboard.html" \
    -p StandardOutput=append:"$log" -p StandardError=append:"$log" "$@" && echo "  $unit started"
}
restart() { systemctl --user stop "$1" 2>/dev/null; sleep 1; start "$@"; }

echo "1/5 database and cluster"
docker start k8s-agent-db aiops-lab-control-plane >/dev/null 2>&1
for _ in $(seq 1 20); do docker exec k8s-agent-db pg_isready -U postgres >/dev/null 2>&1 && break; sleep 2; done
docker exec k8s-agent-db pg_isready -U postgres >/dev/null 2>&1 || { echo "database not ready"; exit 1; }
for _ in $(seq 1 30); do "$KUBECTL" get nodes --no-headers 2>/dev/null | grep -q " Ready" && break; sleep 3; done
"$KUBECTL" get nodes --no-headers || { echo "cluster not ready"; exit 1; }

echo "2/5 collector and watcher"
ns_args=(); for n in $NAMESPACES; do ns_args+=(--namespace "$n"); done
start k8s-collector collector "$PY" -m app.collector.run "${ns_args[@]}" --interval 15
start k8s-watch watch "$PY" -m app.remediation.watch --namespace remediate-lab --interval 60

echo "3/5 dashboard and API (restarted together so they agree on the dashboard file)"
exp=""; [ -f "$EXPERIMENT_LOG" ] && exp="--experiment-log $(printf %q "$EXPERIMENT_LOG")"
restart k8s-dashboard dashboard /bin/bash -c "while true; do $(printf %q "$PY") -m app.predictor.dashboard \
  --out $(printf %q "$DATA_DIR/dashboard.html") --report docs/results/final-memory.md \
  --signals-report docs/results/final-storage-jobs.md $exp >/dev/null 2>&1; sleep 60; done"
restart k8s-api api "$PY" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

echo "4/5 exporter for Prometheus (read-only, on the kind network only)"
GW="$(docker network inspect kind -f '{{range .IPAM.Config}}{{.Gateway}} {{end}}' 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | head -1)"
if [ -n "$GW" ]; then start k8s-exporter exporter "$PY" -m app.metrics.exporter --host "$GW" --port 9105
else echo "  kind network not found: exporter not started"; fi

echo "5/5 Prometheus and Grafana on localhost (if installed)"
if "$KUBECTL" get namespace monitoring >/dev/null 2>&1; then
  start k8s-pf-prometheus pf-prometheus "$KUBECTL" -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090
  start k8s-pf-grafana pf-grafana "$KUBECTL" -n monitoring port-forward svc/monitoring-grafana 3000:80
else echo "  monitoring not installed (see README: Monitoring)"; fi

for _ in $(seq 1 30); do [ -s "$DATA_DIR/dashboard.html" ] && curl -sf localhost:8000/health >/dev/null && break; sleep 3; done
echo
for s in k8s-collector k8s-watch k8s-dashboard k8s-api k8s-exporter k8s-pf-prometheus k8s-pf-grafana; do
  printf '  %-18s %s\n' "$s" "$(systemctl --user is-active "$s" 2>/dev/null)"; done
echo
echo "  approval page:  http://127.0.0.1:8000/ui"
running k8s-pf-grafana && echo "  Grafana:        http://localhost:3000   Prometheus: http://localhost:9090"
[ "${OPEN:-1}" = 1 ] && command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:8000/ui" >/dev/null 2>&1 &
exit 0

#!/usr/bin/env bash
# Build the whole lab on a fresh Linux machine, in one command. Safe to run again: every step checks first
# and skips what already exists (an existing cluster, database or namespace is never changed).
#
#   scripts/setup-lab.sh --check             report what exists and what would be done; change nothing
#   scripts/setup-lab.sh                     build: kind cluster, metrics-server, PostgreSQL, Python, workloads
#   scripts/setup-lab.sh --with-monitoring   also Prometheus and Grafana (chart version >= 14 days old)
#
# Needs: Docker (running, usable without sudo), kind, kubectl, Python 3.10+, curl; helm and openssl for monitoring.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
CLUSTER="${CLUSTER:-aiops-lab}"
NODE_IMAGE="${NODE_IMAGE:-kindest/node:v1.34.0}"
METRICS_SERVER="${METRICS_SERVER:-v0.7.2}"
PG_IMAGE="${PG_IMAGE:-postgres:16}"
DB_CONTAINER="${DB_CONTAINER:-k8s-agent-db}"
DB_PASSWORD="${DB_PASSWORD:-password}"           # lab only; the database listens on 127.0.0.1 only
VENV="${VENV:-$REPO/venv}"
CHECK=0; MONITORING=0
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;; --with-monitoring) MONITORING=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
skip() { say "  exists: $*  (skipped)"; }
todo() { if [ "$CHECK" = 1 ]; then say "  would:  $*"; return 1; fi; say "  doing:  $*"; return 0; }
fail() { say "  ERROR: $*"; exit 1; }

say "1/7 prerequisites"
missing=0
for c in docker kind kubectl python3 curl; do command -v "$c" >/dev/null || { say "  missing: $c"; missing=1; }; done
if [ "$MONITORING" = 1 ]; then for c in helm openssl; do command -v "$c" >/dev/null || { say "  missing: $c"; missing=1; }; done; fi
[ "$missing" = 0 ] || fail "install the missing tools (see README: Try it yourself)"
docker info >/dev/null 2>&1 || fail "Docker is not running, or needs sudo (add yourself to the docker group)"
pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail "Python 3.10+ needed, found $pyver"
say "  ok: docker, kind, kubectl, python $pyver, curl"

say "2/7 kind cluster '$CLUSTER'"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then skip "cluster $CLUSTER"
elif todo "create cluster $CLUSTER ($NODE_IMAGE)"; then
  kind create cluster --name "$CLUSTER" --image "$NODE_IMAGE" --wait 120s || fail "kind create cluster failed"
fi
[ "$CHECK" = 1 ] && ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER" && say "  (later steps assume the cluster exists)"
CTX="kind-$CLUSTER"

say "3/7 metrics-server $METRICS_SERVER (15 s resolution)"
if kubectl --context "$CTX" -n kube-system get deployment metrics-server >/dev/null 2>&1; then skip "metrics-server"
elif todo "install metrics-server $METRICS_SERVER, allow the kind kubelet's self-signed certificate"; then
  kubectl --context "$CTX" apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/$METRICS_SERVER/components.yaml" \
    || fail "metrics-server install failed"
  kubectl --context "$CTX" -n kube-system patch deployment metrics-server --type=json \
    -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
  kubectl --context "$CTX" -n kube-system rollout status deployment/metrics-server --timeout=180s
fi

say "4/7 PostgreSQL container '$DB_CONTAINER' (localhost only)"
if docker container inspect "$DB_CONTAINER" >/dev/null 2>&1; then skip "container $DB_CONTAINER"
  docker start "$DB_CONTAINER" >/dev/null 2>&1
elif todo "run $PG_IMAGE as $DB_CONTAINER on 127.0.0.1:5432, data in volume $DB_CONTAINER-data"; then
  docker run -d --name "$DB_CONTAINER" --restart unless-stopped -e POSTGRES_PASSWORD="$DB_PASSWORD" \
    -p 127.0.0.1:5432:5432 -v "$DB_CONTAINER-data:/var/lib/postgresql/data" "$PG_IMAGE" >/dev/null || fail "docker run failed"
fi
if [ "$CHECK" = 0 ]; then
  for _ in $(seq 1 30); do docker exec "$DB_CONTAINER" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 2; done
  docker exec "$DB_CONTAINER" pg_isready -U postgres >/dev/null 2>&1 || fail "database not ready"
fi
for db in k8s_agent k8s_agent_test; do
  if docker exec "$DB_CONTAINER" psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" 2>/dev/null | grep -q 1; then
    skip "database $db"
  elif todo "create database $db"; then
    docker exec "$DB_CONTAINER" psql -U postgres -qc "CREATE DATABASE $db" || fail "create database $db failed"
  fi
done

say "5/7 Python environment and database schema"
if [ -x "$VENV/bin/python" ]; then skip "virtual environment $VENV"
elif todo "create $VENV and install backend/requirements.txt (several minutes)"; then
  python3 -m venv "$VENV" && "$VENV/bin/pip" install -q -r backend/requirements.txt || fail "pip install failed"
fi
if todo "apply database migrations (no-op when up to date)"; then
  for db in k8s_agent k8s_agent_test; do
    DATABASE_URL="postgresql://postgres:$DB_PASSWORD@localhost:5432/$db" PYTHONPATH=backend \
      "$VENV/bin/python" -m alembic upgrade head >/dev/null || fail "migrations failed on $db"
  done
fi

say "6/7 lab workloads"
apply_ns() {   # apply_ns <namespace> <manifest...>: only when the namespace does not exist yet
  local ns="$1"; shift
  if kubectl --context "$CTX" get namespace "$ns" >/dev/null 2>&1; then skip "namespace $ns"
  elif todo "deploy namespace $ns"; then
    for f in "$@"; do kubectl --context "$CTX" apply -f "$f" >/dev/null || fail "kubectl apply $f failed"; done
  fi
}
apply_ns predict-lab docs/testing/predict-lab/predict-lab.yaml docs/testing/predict-lab/predict-lab-more.yaml
apply_ns remediate-lab docs/testing/remediate-lab/remediate-lab.yaml
apply_ns pattern-lab docs/testing/pattern-lab/pattern-lab.yaml

say "7/7 monitoring (Prometheus and Grafana)"
if [ "$MONITORING" = 0 ]; then say "  not requested (add --with-monitoring)"
elif kubectl --context "$CTX" get namespace monitoring >/dev/null 2>&1; then skip "namespace monitoring"
elif todo "install kube-prometheus-stack (version >= 14 days old), alert rules and dashboard"; then
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1
  helm repo update prometheus-community >/dev/null || fail "helm repo update failed"
  V="$("$VENV/bin/python" docs/monitoring/pick_chart_version.py)" || fail "could not pick a chart version"
  pw="$HOME/.k8s-failure-lab-grafana-admin"; ( umask 077; openssl rand -base64 18 > "$pw" )
  helm install monitoring prometheus-community/kube-prometheus-stack --version "$V" -n monitoring --create-namespace \
    -f docs/monitoring/kube-prometheus-stack-values.yaml --set grafana.adminPassword="$(cat "$pw")" \
    --kube-context "$CTX" --wait --timeout 20m || fail "helm install failed"
  kubectl --context "$CTX" apply -f docs/monitoring/forecast-rules.yaml -f docs/monitoring/grafana-dashboard.yaml >/dev/null
  say "  Grafana admin password saved in $pw (readable only by you)"
fi

if [ "$CHECK" = 1 ]; then say; say "check only: nothing was changed"; exit 0; fi
say; say "setup complete: starting the services"
[ "${START:-1}" = 1 ] && exec "$REPO/scripts/start.sh"
exit 0

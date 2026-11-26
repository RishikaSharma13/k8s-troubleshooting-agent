#!/usr/bin/env bash
# Open the lab. The system runs inside the cluster (Helm release, charts/k8s-failure-agent), so this only starts
# the kind cluster if needed, waits until the release is healthy, and opens local windows into it:
#   approval page  http://127.0.0.1:8080/ui   (k8s-pf-api)
#   Prometheus     http://localhost:9090      (k8s-pf-prometheus, if monitoring is installed)
#   Grafana        http://localhost:3000      (k8s-pf-grafana, if monitoring is installed)
#   Argo CD        https://localhost:8443     (k8s-pf-argocd, if Argo CD is installed)
# The port-forwards restart by themselves if a pod is replaced. Safe to run again. Close with scripts/stop.sh.
#
# Settings (optional file ~/.k8s-failure-lab.env): RELEASE (demo), NAMESPACE (failure-agent),
# CLUSTER_CONTAINER (aiops-lab-control-plane).
set -u
[ -f "$HOME/.k8s-failure-lab.env" ] && . "$HOME/.k8s-failure-lab.env"
RELEASE="${RELEASE:-demo}"
NAMESPACE="${NAMESPACE:-failure-agent}"
CLUSTER_CONTAINER="${CLUSTER_CONTAINER:-aiops-lab-control-plane}"
FULLNAME="${FULLNAME:-$RELEASE-k8s-failure-agent}"
KUBECTL="$(command -v kubectl)" || { echo "kubectl not found"; exit 1; }

running() { systemctl --user is-active --quiet "$1"; }
forward() {   # forward <unit> <namespace> <service> <local:remote>, restarted automatically if it drops
  local unit="$1"
  running "$unit" && { echo "  $unit already running"; return; }
  systemctl --user reset-failed "$unit" 2>/dev/null
  systemd-run --user --quiet --unit="$unit" -p Restart=always -p RestartSec=3 \
    "$KUBECTL" -n "$2" port-forward "svc/$3" "$4" && echo "  $unit started"
}

echo "1/3 cluster"
docker start "$CLUSTER_CONTAINER" >/dev/null 2>&1
for _ in $(seq 1 40); do "$KUBECTL" get nodes --no-headers 2>/dev/null | grep -q " Ready" && break; sleep 3; done
"$KUBECTL" get nodes --no-headers || { echo "cluster not ready"; exit 1; }

echo "2/3 release $RELEASE in namespace $NAMESPACE"
helm status "$RELEASE" -n "$NAMESPACE" >/dev/null 2>&1 || { echo "  release not installed (see README: Run it in Kubernetes)"; exit 1; }
"$KUBECTL" -n "$NAMESPACE" wait --for=condition=Available deploy -l "app.kubernetes.io/instance=$RELEASE" --timeout=300s \
  | sed 's/^/  /' || echo "  some components are not ready yet: kubectl -n $NAMESPACE get pods"

echo "3/3 local windows into the cluster"
forward k8s-pf-api "$NAMESPACE" "$FULLNAME-api" 8080:8000
if "$KUBECTL" get namespace monitoring >/dev/null 2>&1; then
  forward k8s-pf-prometheus monitoring monitoring-kube-prometheus-prometheus 9090:9090
  forward k8s-pf-grafana monitoring monitoring-grafana 3000:80
fi
if "$KUBECTL" get namespace argocd >/dev/null 2>&1; then
  forward k8s-pf-argocd argocd argocd-server 8443:443
fi
sleep 3
echo
for s in k8s-pf-api k8s-pf-prometheus k8s-pf-grafana k8s-pf-argocd; do printf '  %-18s %s\n' "$s" "$(systemctl --user is-active "$s" 2>/dev/null)"; done
echo
echo "  approval page:  http://127.0.0.1:8080/ui"
running k8s-pf-grafana && echo "  Grafana:        http://localhost:3000   Prometheus: http://localhost:9090"
running k8s-pf-argocd && echo "  Argo CD:        https://localhost:8443 (self-signed certificate; user admin)"
[ "${OPEN:-1}" = 1 ] && command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:8080/ui" >/dev/null 2>&1 &
exit 0

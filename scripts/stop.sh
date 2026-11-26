#!/usr/bin/env bash
# Close the local windows into the cluster. The system keeps running in the cluster; nothing is deleted.
# (Also stops the services of the retired laptop setup, if any are still running.)
for s in k8s-pf-api k8s-pf-argocd k8s-pf-grafana k8s-pf-prometheus k8s-exporter k8s-api k8s-dashboard k8s-watch k8s-collector k8s-experiment; do
  if systemctl --user is-active --quiet "$s"; then systemctl --user stop "$s" && echo "  stopped $s"; fi
done
echo "local windows closed; the system keeps running in the cluster (helm status demo -n failure-agent)"

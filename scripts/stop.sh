#!/usr/bin/env bash
# Stop every background service of the lab (data, cluster and database are kept). Start again with scripts/start.sh.
for s in k8s-pf-grafana k8s-pf-prometheus k8s-exporter k8s-api k8s-dashboard k8s-watch k8s-collector k8s-experiment; do
  if systemctl --user is-active --quiet "$s"; then systemctl --user stop "$s" && echo "  stopped $s"; fi
done
echo "services stopped; the cluster, database and all data are unchanged"

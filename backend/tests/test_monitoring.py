"""The monitoring configuration must stay consistent with the exporter."""
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.metrics import exporter

MON = Path(__file__).resolve().parents[2] / "docs" / "monitoring"
yaml = pytest.importorskip("yaml")


def test_every_metric_used_by_rules_and_dashboard_is_exported():
    used = set(re.findall(r"k8s_failure_[a-z_]+", (MON / "forecast-rules.yaml").read_text() +
                          (MON / "grafana-dashboard.yaml").read_text()))
    exported = {f"k8s_failure_{name}" for name in exporter.HELP}
    assert used and used <= exported, used - exported


def test_rules_compare_forecast_and_threshold():
    rules = yaml.safe_load((MON / "forecast-rules.yaml").read_text())["spec"]["groups"][0]["rules"]
    by = {r["labels"]["method"]: r for r in rules}
    assert by["forecast"]["expr"] == "k8s_failure_seconds_to_limit < 900"
    assert by["threshold"]["expr"] == "k8s_failure_memory_usage_ratio > 0.8"


def test_exporter_discovered_by_service_monitor_and_no_password_in_the_repository():
    """The exporter runs in the cluster: no fixed scrape address; the chart's ServiceMonitor carries the label
    this Prometheus selects (kube-prometheus-stack selects ServiceMonitors by its release label)."""
    values = yaml.safe_load((MON / "kube-prometheus-stack-values.yaml").read_text())
    assert "additionalScrapeConfigs" not in values["prometheus"]["prometheusSpec"]
    chart_values = yaml.safe_load((MON.parents[1] / "charts" / "k8s-failure-agent" / "values.yaml").read_text())
    assert chart_values["serviceMonitor"]["enabled"] is True
    assert chart_values["serviceMonitor"]["labels"] == {"release": "monitoring"}
    assert "adminPassword" not in (values.get("grafana") or {})       # generated at install time, never committed


def test_start_script_only_opens_windows_into_the_cluster():
    text = (MON.parents[1] / "scripts" / "start.sh").read_text()
    for app_process in ("app.collector.run", "app.remediation.watch", "uvicorn", "app.metrics.exporter"):
        assert app_process not in text                               # the system runs in the cluster now
    assert "port-forward" in text and "Restart=always" in text

def test_dashboard_is_valid_json_with_unique_panels():
    cm = yaml.safe_load((MON / "grafana-dashboard.yaml").read_text())
    assert cm["metadata"]["labels"]["grafana_dashboard"] == "1"
    dash = json.loads(cm["data"]["failure-forecast.json"])
    ids = [p["id"] for p in dash["panels"]]
    assert len(ids) == len(set(ids)) and dash["uid"] == "k8s-failure-forecast"


def test_chart_version_picker_skips_recent_releases():
    spec = importlib.util.spec_from_file_location("pick", MON / "pick_chart_version.py")
    pick = importlib.util.module_from_spec(spec); spec.loader.exec_module(pick)
    index = ("apiVersion: v1\nentries:\n  kube-prometheus-stack:\n"
             "  - apiVersion: v2\n    created: \"2026-11-17T10:00:00Z\"\n    dependencies:\n    - name: grafana\n      version: 9.0.0\n    version: 80.2.0\n"
             "  - apiVersion: v2\n    created: \"2026-10-30T10:00:00Z\"\n    version: 80.1.0\n"
             "  - apiVersion: v2\n    created: \"2026-10-01T10:00:00Z\"\n    version: 79.0.0\n"
             "  prometheus:\n  - created: \"2026-11-01T10:00:00Z\"\n    version: 99.0.0\n")
    version, _ = pick.pick(index, datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert version == "80.1.0"                     # 80.2.0 is only 2 days old; other charts are ignored


def test_picker_reads_helm_nanosecond_timestamps():
    """Seen on 19 Nov: Helm's index has 9 decimals, which Python 3.10 cannot parse directly."""
    spec = importlib.util.spec_from_file_location("pick", MON / "pick_chart_version.py")
    pick = importlib.util.module_from_spec(spec); spec.loader.exec_module(pick)
    t = pick.parse_time("2026-11-18T22:44:41.030554039+00:00")
    assert (t.year, t.microsecond, t.utcoffset().total_seconds()) == (2026, 30554, 0)
    assert pick.parse_time("2026-10-01T10:00:00Z").tzinfo is not None
    index = ("entries:\n  kube-prometheus-stack:\n"
             "  - created: \"2026-11-18T22:44:41.030554039+00:00\"\n    version: 80.3.0\n"
             "  - created: \"2026-10-20T08:12:03.123456789+00:00\"\n    version: 80.0.1\n")
    assert pick.pick(index, datetime(2026, 11, 19, tzinfo=timezone.utc))[0] == "80.0.1"


def test_verdict_colours_mean_what_they_say():
    cm = yaml.safe_load((MON / "grafana-dashboard.yaml").read_text())
    panels = {p["title"]: p for p in json.loads(cm["data"]["failure-forecast.json"])["panels"]}
    rules = {o["matcher"]["options"]: o["properties"][0]["value"]["fixedColor"]
             for o in panels["Incidents by verdict"]["fieldConfig"]["overrides"]}
    assert rules["^(CONFIRMED|AVOIDED)$"] == "green" and rules["^NOT_(CONFIRMED|AVOIDED)$"] == "red"


def test_start_script_is_valid_bash():
    import shutil, subprocess
    script = Path(__file__).resolve().parents[2] / "scripts" / "start.sh"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    if shutil.which("shellcheck"):
        assert subprocess.run(["shellcheck", "-S", "warning", str(script)], capture_output=True).returncode == 0


def test_time_series_have_one_line_per_workload_not_per_pod():
    """A restarted pod gets a new name; grouping by deployment keeps one continuous line per workload."""
    cm = yaml.safe_load((MON / "grafana-dashboard.yaml").read_text())
    for panel in json.loads(cm["data"]["failure-forecast.json"])["panels"]:
        if panel["type"] != "timeseries":
            continue
        for t in panel["targets"]:
            if "k8s_failure_" in t["expr"]:
                assert "by (namespace, deployment)" in t["expr"], t["expr"]


def test_picker_handles_other_charts():
    spec = importlib.util.spec_from_file_location("pick", MON / "pick_chart_version.py")
    pick = importlib.util.module_from_spec(spec); spec.loader.exec_module(pick)
    index = ("entries:\n  argo-cd:\n"
             "  - created: \"2026-11-25T10:00:00.1Z\"\n    version: 10.20.0\n"
             "  - created: \"2026-11-11T15:05:41.46475242Z\"\n    version: 10.18.0\n"
             "  - created: \"2026-11-11T09:00:00Z\"\n    version: 10.17.2\n"
             "  kube-prometheus-stack:\n  - created: \"2026-10-01T10:00:00Z\"\n    version: 92.0.0\n")
    now = datetime(2026, 11, 26, tzinfo=timezone.utc)
    assert pick.pick(index, now, chart="argo-cd")[0] == "10.18.0"      # newest of those at least 14 days old
    assert pick.pick(index, now)[0] == "92.0.0"                        # the default chart is unchanged


def test_argocd_settings_keep_tls_and_switch_off_unused_parts():
    values = yaml.safe_load((MON.parents[0] / "gitops" / "argocd-values.yaml").read_text())
    assert values["dex"]["enabled"] is False and values["notifications"]["enabled"] is False
    assert values["configs"]["cm"]["timeout.reconciliation"] == "60s"
    assert "server.insecure" not in str(values)                         # the server keeps its own TLS


def test_picker_reads_any_number_of_decimals():
    """Seen on 26 Nov on Python 3.10: '.1' failed to parse (3.10 accepts only 3 or 6 decimals; 3.11+ accept any)."""
    spec = importlib.util.spec_from_file_location("pick", MON / "pick_chart_version.py")
    pick = importlib.util.module_from_spec(spec); spec.loader.exec_module(pick)
    for frac, micro in ((".1", 100000), (".12345", 123450), (".123456", 123456), (".46475242", 464752), (".030554039", 30554)):
        t = pick.parse_time(f"2026-11-25T10:00:00{frac}Z")
        assert t.microsecond == micro and t.utcoffset().total_seconds() == 0, frac
    assert pick.parse_time("2026-11-25T10:00:00Z").microsecond == 0
    assert pick.parse_time("2026-11-25T10:00:00.5+05:30").utcoffset().total_seconds() == 19800

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


def test_scrape_target_and_no_password_in_the_repository():
    text = (MON / "kube-prometheus-stack-values.yaml").read_text()
    values = yaml.safe_load(text)
    target = values["prometheus"]["prometheusSpec"]["additionalScrapeConfigs"][0]["static_configs"][0]["targets"][0]
    assert target.endswith(":9105")                                   # the exporter's default port
    assert "adminPassword" not in (values.get("grafana") or {})       # generated at install time, never committed


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

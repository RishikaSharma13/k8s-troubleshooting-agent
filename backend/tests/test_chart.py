"""The Helm chart lints and renders as intended (skipped where helm is not installed)."""
import shutil
import subprocess
from pathlib import Path

import pytest

CHART = Path(__file__).resolve().parents[2] / "charts" / "k8s-failure-agent"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="needs helm")
yaml = pytest.importorskip("yaml")


def render(*args, release="demo"):
    r = subprocess.run(["helm", "template", release, str(CHART), "-n", "failure-agent", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def by_kind(docs, kind):
    return [d for d in docs if d["kind"] == kind]


def test_chart_lints():
    r = subprocess.run(["helm", "lint", str(CHART)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_settings_from_values_and_standard_labels():
    cm = by_kind(render(), "ConfigMap")[0]
    assert cm["metadata"]["name"] == "demo-k8s-failure-agent-settings"
    assert cm["data"]["NAMESPACES"] == "predict-lab,remediate-lab,pattern-lab"
    labels = cm["metadata"]["labels"]
    assert labels["app.kubernetes.io/managed-by"] == "Helm" and labels["app.kubernetes.io/instance"] == "demo"


def test_overrides_and_naming_rules():
    cm = by_kind(render("--set", "settings.namespaces={predict-lab}", "--set", "settings.collectorIntervalSeconds=30"), "ConfigMap")[0]
    assert (cm["data"]["NAMESPACES"], cm["data"]["COLLECTOR_INTERVAL_SECONDS"]) == ("predict-lab", "30")
    assert by_kind(render(release="k8s-failure-agent"), "ConfigMap")[0]["metadata"]["name"] == "k8s-failure-agent-settings"
    assert by_kind(render("--set", "fullnameOverride=agent"), "ConfigMap")[0]["metadata"]["name"] == "agent-settings"

"""Tests for scripts/setup-lab.sh, with stand-in docker/kind/kubectl/helm that record every call."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "setup-lab.sh"
CHANGES = ("kind create", "kind load", "kubectl --context kind-aiops-lab apply", "docker run", "docker build",
           "CREATE DATABASE", "helm install", "patch deployment")
BASIC = ("bash", "sed", "seq", "sleep", "grep", "cat", "dirname", "basename", "head", "touch", "date", "tr", "python3", "git")

STUB = r'''#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
exists() { [ "$STUB_STATE" = exists ] || [ -f "$STUB_LOG.$1" ]; }    # each resource has its own marker
case "$(basename "$0") $*" in
  "kind get clusters"*)          exists cluster && echo aiops-lab; exit 0 ;;
  "kind create"*)                touch "$STUB_LOG.cluster"; exit 0 ;;
  "docker info"*)                exit 0 ;;
  *"crictl images"*)             [ "$STUB_STATE" = exists ] && echo "docker.io/library/k8s-failure-agent   $STUB_TAG   abc   185MB"; exit 0 ;;
  "docker container inspect"*)   exists container && exit 0; exit 1 ;;
  "docker run"*)                 touch "$STUB_LOG.container"; exit 0 ;;
  *"pg_isready"*)                exit 0 ;;
  *"SELECT 1 FROM pg_database"*) [ "$STUB_STATE" = exists ] && echo 1; exit 0 ;;
  "helm status"*)                [ "$STUB_STATE" = exists ] && exit 0; exit 1 ;;
  "kubectl"*" get "*)            [ "$STUB_STATE" = exists ] && exit 0; exit 1 ;;
  *)                             exit 0 ;;
esac
'''
TAG = "sha-" + subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()


def run(tmp_path, state, *args, tools=("docker", "kind", "kubectl", "helm", "openssl", "curl"), index=None):
    """Run the script with a SEALED search path: stand-ins for every cluster or network tool, plus a few basic
    utilities linked one by one. Never all of /usr/bin, where a real helm or kubectl could be found and run
    against a real cluster (seen on 23 Nov: a real helm on the developer's machine)."""
    bin_ = tmp_path / "bin"; bin_.mkdir(exist_ok=True)
    sysbin = tmp_path / "sysbin"; sysbin.mkdir(exist_ok=True)
    for b in BASIC:
        real = shutil.which(b)
        if real and not (sysbin / b).exists():
            (sysbin / b).symlink_to(real)
    for t in tools:
        (bin_ / t).write_text(STUB); (bin_ / t).chmod(0o755)
    venv = tmp_path / "venv" / "bin"; venv.mkdir(parents=True, exist_ok=True)
    (venv / "python").write_text(STUB); (venv / "python").chmod(0o755)
    if index is not None:                                   # the Helm repository index the version picker reads
        cache = tmp_path / ".cache" / "helm" / "repository"; cache.mkdir(parents=True, exist_ok=True)
        (cache / "prometheus-community-index.yaml").write_text(index)
    log = tmp_path / "calls.log"
    for f in [log, *tmp_path.glob("calls.log.*")]:
        f.unlink(missing_ok=True)
    env = {**os.environ, "PATH": f"{bin_}:{sysbin}", "STUB_LOG": str(log), "STUB_STATE": state, "STUB_TAG": TAG,
           "VENV": str(tmp_path / "venv"), "START": "0", "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    return r, (log.read_text() if log.exists() else "")


def index_with(version):
    return ("apiVersion: v1\nentries:\n  kube-prometheus-stack:\n"
            f"  - created: \"2026-10-01T10:00:00.123456789Z\"\n    version: {version}\n")


pytestmark = pytest.mark.skipif(shutil.which("python3") is None or shutil.which("git") is None, reason="needs python3 and git")


def test_check_mode_changes_nothing_even_on_an_empty_machine(tmp_path):
    r, calls = run(tmp_path, "empty", "--check", "--with-monitoring", "--dev")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would:  create cluster aiops-lab" in r.stdout and "would:  install charts/k8s-failure-agent" in r.stdout
    assert "nothing was changed" in r.stdout
    assert not [c for c in CHANGES if c in calls], calls


def test_existing_lab_is_never_changed(tmp_path):
    r, calls = run(tmp_path, "exists")
    assert r.returncode == 0, r.stdout + r.stderr
    for what in ("cluster aiops-lab", "metrics-server", "namespace predict-lab", "namespace pattern-lab",
                 f"image k8s-failure-agent:{TAG}", "release demo"):
        assert f"exists: {what}" in r.stdout, what
    assert "not requested (add --dev)" in r.stdout
    assert not [c for c in CHANGES if c in calls], calls


def test_empty_machine_is_built_in_order(tmp_path):
    r, calls = run(tmp_path, "empty")
    assert r.returncode == 0, r.stdout + r.stderr
    lines = calls.splitlines()
    idx = lambda s: next(i for i, l in enumerate(lines) if s in l)
    order = [idx("kind create cluster --name aiops-lab --image kindest/node:v1.34.0"),
             idx("metrics-server/releases/download/v0.7.2"), idx("predict-lab.yaml"),
             idx(f"docker build -t k8s-failure-agent:{TAG}"), idx(f"kind load docker-image k8s-failure-agent:{TAG}"),
             idx("helm install demo charts/k8s-failure-agent")]
    assert order == sorted(order), order
    assert f"--set-string image.tag={TAG}" in calls                 # a commit id is never read as a number
    assert "docker run" not in calls and "helm install monitoring" not in calls   # only when asked


def test_monitoring_is_installed_before_the_release(tmp_path):
    """The chart creates its ServiceMonitor only if Prometheus already exists, so the order matters."""
    r, calls = run(tmp_path, "empty", "--with-monitoring", index=index_with("80.1.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    lines = calls.splitlines()
    mon = next(i for i, l in enumerate(lines) if "helm install monitoring" in l and "--version 80.1.0" in l)
    app = next(i for i, l in enumerate(lines) if "helm install demo" in l)
    assert mon < app


def test_unpinned_chart_version_is_refused_before_helm_install(tmp_path):
    r, calls = run(tmp_path, "empty", "--with-monitoring", index=index_with("latest"))
    assert r.returncode == 1 and "refusing to install an unpinned chart" in r.stdout
    assert "helm install" not in calls


def test_developer_environment_only_with_dev(tmp_path):
    r, calls = run(tmp_path, "empty", "--dev")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-p 127.0.0.1:5432:5432" in calls                     # the test database is not exposed on the network
    assert calls.count("CREATE DATABASE") == 2 and calls.count("alembic upgrade head") == 2


def test_missing_tool_is_reported_before_anything_runs(tmp_path):
    r, calls = run(tmp_path, "empty", tools=("docker", "kind", "kubectl", "curl"))
    assert r.returncode == 1 and "missing: helm" in r.stdout
    assert calls == ""                                           # stopped before touching anything


def test_sealed_path_cannot_reach_real_tools(tmp_path):
    """Even where helm and kubectl are installed for real, the tests can only ever call stand-ins."""
    run(tmp_path, "exists", "--check")
    env_path = f"{tmp_path / 'bin'}:{tmp_path / 'sysbin'}"
    found = subprocess.run(["bash", "-c", "command -v helm kubectl kind docker"], env={"PATH": env_path},
                           capture_output=True, text=True).stdout.split()
    assert found and all(f.startswith(str(tmp_path / "bin")) for f in found), found


def test_stop_script_only_stops_services():
    text = (REPO / "scripts" / "stop.sh").read_text()
    assert subprocess.run(["bash", "-n", str(REPO / "scripts" / "stop.sh")]).returncode == 0
    for destructive in ("kind delete", "docker rm", "volume rm", "DROP DATABASE", "helm uninstall"):
        assert destructive not in text

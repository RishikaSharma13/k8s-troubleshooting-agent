"""The GitOps setup stays safe and consistent: what Argo CD deploys, from where, and into which namespaces."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
yaml = pytest.importorskip("yaml")
REQUIRED = os.environ.get("REQUIRE_CHART_TOOLS") == "1"
needs_helm = pytest.mark.skipif(shutil.which("helm") is None and not REQUIRED, reason="needs helm")

APP = yaml.safe_load((ROOT / "deploy/argocd/application.yaml").read_text())
PROJECT = yaml.safe_load((ROOT / "deploy/argocd/project.yaml").read_text())
SRC = APP["spec"]["source"]
VALUES = (ROOT / SRC["path"] / SRC["helm"]["valueFiles"][0]).resolve()


def render():
    r = subprocess.run(["helm", "template", SRC["helm"]["releaseName"], str(ROOT / SRC["path"]),
                        "-n", APP["spec"]["destination"]["namespace"], "-f", str(VALUES)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_values_file_resolves_inside_the_repository():
    assert VALUES.is_file() and ROOT in VALUES.parents


def test_lab_values_never_let_the_chart_generate_a_password():
    """Under Argo CD, a generated password would change on every render and overwrite the real one."""
    values = yaml.safe_load(VALUES.read_text())
    assert values["database"]["existingSecret"]
    assert str(values["image"]["tag"]).startswith("sha-")             # a published, commit-tagged image


@needs_helm
def test_argocd_render_is_reproducible_and_creates_no_secret():
    first, second = render(), render()
    assert first == second
    assert not [d for d in yaml.safe_load_all(first) if d and d["kind"] == "Secret"]


@needs_helm
def test_project_allows_exactly_the_namespaces_and_cluster_kinds_the_chart_needs():
    docs = [d for d in yaml.safe_load_all(render()) if d]
    used = {d["metadata"].get("namespace", APP["spec"]["destination"]["namespace"]) for d in docs
            if d["kind"] not in ("ClusterRole", "ClusterRoleBinding")}
    allowed = {d["namespace"] for d in PROJECT["spec"]["destinations"]}
    assert used == allowed, (used, allowed)
    cluster_kinds = {d["kind"] for d in docs if d["kind"] in ("ClusterRole", "ClusterRoleBinding")}
    whitelist = {w["kind"] for w in PROJECT["spec"]["clusterResourceWhitelist"]}
    assert cluster_kinds <= whitelist


def test_application_is_fenced_to_this_repository():
    assert PROJECT["spec"]["sourceRepos"] == [SRC["repoURL"]]
    assert APP["spec"]["project"] == PROJECT["metadata"]["name"] != "default"
    assert SRC["targetRevision"] == "main" and SRC["helm"]["releaseName"] == "demo"

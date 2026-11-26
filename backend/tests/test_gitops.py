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


ROOT_DOCS = [d for d in yaml.safe_load_all((ROOT / "deploy/argocd/bootstrap/root.yaml").read_text()) if d]
ROOT_PROJECT = next(d for d in ROOT_DOCS if d["kind"] == "AppProject")
ROOT_APP = next(d for d in ROOT_DOCS if d["kind"] == "Application")


def test_root_is_fenced_to_argocd_objects_in_the_argocd_namespace():
    spec = ROOT_PROJECT["spec"]
    assert spec["destinations"] == [{"server": "https://kubernetes.default.svc", "namespace": "argocd"}]
    assert spec["clusterResourceWhitelist"] == []                             # nothing cluster-wide
    assert {(w["group"], w["kind"]) for w in spec["namespaceResourceWhitelist"]} == {
        ("argoproj.io", "Application"), ("argoproj.io", "AppProject")}
    assert ROOT_APP["spec"]["project"] == "gitops-root"


def test_root_applies_only_what_its_fence_allows():
    """Every file the root syncs (top level of deploy/argocd) must be an Argo CD object in namespace argocd."""
    src = ROOT_APP["spec"]["source"]
    assert src["path"] == "deploy/argocd" and src["directory"]["recurse"] is False
    files = sorted((ROOT / src["path"]).glob("*.yaml"))
    assert [f.name for f in files] == ["application.yaml", "project.yaml"]  # the bootstrap folder is not synced
    for f in files:
        for d in (x for x in yaml.safe_load_all(f.read_text()) if x):
            assert d["apiVersion"].startswith("argoproj.io/") and d["kind"] in ("Application", "AppProject"), f.name
            assert d["metadata"]["namespace"] == "argocd", f.name


def test_root_heals_itself_but_never_prunes():
    """Deleting application.yaml from Git must not delete the running system."""
    policy = ROOT_APP["spec"]["syncPolicy"]["automated"]
    assert policy["selfHeal"] is True and policy["prune"] is False


def test_scripts_find_the_release_whoever_manages_it():
    for script in ("scripts/start.sh", "scripts/setup-lab.sh"):
        text = (ROOT / script).read_text()
        assert "helm status" not in text, script                             # there is no Helm release under Argo CD
        assert "get deployment" in text and "-collector" in text, script   # looks for the collector Deployment
    assert "-n argocd get application" in (ROOT / "scripts/start.sh").read_text()

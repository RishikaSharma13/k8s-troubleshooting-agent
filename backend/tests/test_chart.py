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


class StrictLoader(yaml.SafeLoader):
    """Rejects duplicate keys, which the default loader silently overwrites (and which the cluster rejects)."""


def _no_duplicates(loader, node, deep=False):
    keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ValueError(f"duplicate keys {dupes} at line {node.start_mark.line + 1}")
    return loader.construct_mapping(node, deep)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def render_text(*args):
    r = subprocess.run(["helm", "template", "demo", str(CHART), "-n", "failure-agent", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_no_duplicate_keys_anywhere():
    """Seen on 23 Nov: pod labels listed name and instance twice; lint and the default YAML loader accepted it."""
    docs = [d for d in yaml.load_all(render_text(), Loader=StrictLoader) if d]
    assert len(docs) >= 4


def test_database_password_secret_is_generated_once_and_kept():
    secret = by_kind(render(), "Secret")[0]
    import base64
    assert len(base64.b64decode(secret["data"]["password"])) == 24
    assert secret["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    tpl = (CHART / "templates" / "db-secret.yaml").read_text()
    assert 'lookup "v1" "Secret"' in tpl                     # reuse the existing password on upgrades


def test_database_statefulset_is_pinned_non_root_and_persistent():
    sts = by_kind(render(), "StatefulSet")[0]
    pod = sts["spec"]["template"]["spec"]
    image = pod["containers"][0]["image"]
    assert ":" in image and not image.endswith((":latest", ":16")), image   # an exact version, not a floating tag
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["automountServiceAccountToken"] is False
    env = {e["name"]: e for e in pod["containers"][0]["env"]}
    assert env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "demo-k8s-failure-agent-db"
    assert sts["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] == "1Gi"
    sel = sts["spec"]["selector"]["matchLabels"]
    assert sel.items() <= sts["spec"]["template"]["metadata"]["labels"].items()   # the pod matches its selector
    svc = by_kind(render(), "Service")[0]
    assert svc["spec"]["clusterIP"] == "None" and svc["spec"]["selector"] == sel


def test_valid_kubernetes_1_34_objects():
    if not shutil.which("kubeconform"):
        pytest.skip("needs kubeconform")
    r = subprocess.run(["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.34.0", "-"],
                       input=render_text(), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_migrations_run_as_a_hook_before_upgrades_and_after_install():
    job = by_kind(render(), "Job")[0]
    ann = job["metadata"]["annotations"]
    assert set(ann["helm.sh/hook"].split(",")) == {"post-install", "pre-upgrade"}
    assert ann["helm.sh/hook-delete-policy"] == "before-hook-creation"       # keeps the last run's logs
    pod = job["spec"]["template"]["spec"]
    assert pod["containers"][0]["command"] == ["alembic", "upgrade", "head"]
    assert "pg_isready" in " ".join(pod["initContainers"][0]["command"])       # waits for the database
    assert job["spec"]["activeDeadlineSeconds"] == 300 and pod["restartPolicy"] == "Never"


def test_migration_container_is_locked_down():
    pod = by_kind(render(), "Job")[0]["spec"]["template"]["spec"]
    sec = pod["containers"][0]["securityContext"]
    assert pod["automountServiceAccountToken"] is False
    assert sec["readOnlyRootFilesystem"] is True and sec["runAsNonRoot"] is True and sec["capabilities"]["drop"] == ["ALL"]
    assert sec["allowPrivilegeEscalation"] is False


def test_password_appears_only_in_its_secret():
    """The connection string is built in the pod from $(DB_PASSWORD); the password itself never leaves the Secret."""
    import base64
    text = render_text()
    docs = [d for d in yaml.safe_load_all(text) if d]
    password = base64.b64decode(by_kind(docs, "Secret")[0]["data"]["password"]).decode()
    others = [d for d in docs if d["kind"] != "Secret"]
    assert password not in yaml.safe_dump_all(others)
    env = {e["name"]: e for e in by_kind(docs, "Job")[0]["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert "$(DB_PASSWORD)" in env["DATABASE_URL"]["value"] and "secretKeyRef" in env["DB_PASSWORD"]["valueFrom"]

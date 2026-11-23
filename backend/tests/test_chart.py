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
    cm = by_kind(render("--set", "settings.namespaces={predict-lab}", "--set", "settings.remediationNamespace=predict-lab",
                        "--set", "settings.collectorIntervalSeconds=30"), "ConfigMap")[0]
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
    svc = next(d for d in by_kind(render(), "Service") if d["metadata"]["labels"]["app.kubernetes.io/component"] == "database")
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


SM = ("--api-versions", "monitoring.coreos.com/v1/ServiceMonitor")


def _deployments(docs):
    return {d["metadata"]["labels"]["app.kubernetes.io/component"]: d for d in by_kind(docs, "Deployment")}


def test_only_the_collector_and_api_get_an_api_token():
    docs = render()
    sas = {d["metadata"]["labels"]["app.kubernetes.io/component"]: d["automountServiceAccountToken"] for d in by_kind(docs, "ServiceAccount")}
    assert sas == {"collector": True, "api": True, "watcher": False, "exporter": False}
    deps = _deployments(docs)
    for name in ("watcher", "exporter"):
        assert deps[name]["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


def test_rbac_is_least_privilege():
    docs = render()
    rules = [(d["kind"], d["metadata"].get("namespace"), r) for d in docs if d["kind"] in ("Role", "ClusterRole") for r in d["rules"]]
    for kind, ns, r in rules:
        assert "*" not in r["verbs"] and "*" not in r["resources"] and "*" not in r["apiGroups"], r
        assert "secrets" not in r["resources"], r
        assert not set(r["verbs"]) & {"create", "delete", "deletecollection", "update", "escalate", "bind", "impersonate"}, r
    writers = [(kind, ns, r["resources"]) for kind, ns, r in rules if "patch" in r["verbs"]]
    assert writers == [("Role", "remediate-lab", ["deployments"])]           # the only write: patch, there, deployments
    collector_ns = sorted(d["metadata"]["namespace"] for d in by_kind(docs, "Role") if d["metadata"]["name"].endswith("-collector"))
    assert collector_ns == ["pattern-lab", "predict-lab", "remediate-lab"]
    cluster = {r["resources"][0]: r for d in by_kind(docs, "ClusterRole") for r in d["rules"]}
    assert cluster["nodes/proxy"]["verbs"] == ["get"]
    assert cluster["namespaces"]["resourceNames"] == ["remediate-lab"] and cluster["namespaces"]["verbs"] == ["get"]


def test_node_stats_permission_can_be_turned_off():
    cluster = [r["resources"][0] for d in by_kind(render("--set", "collector.nodeStats=false"), "ClusterRole") for r in d["rules"]]
    assert cluster == ["namespaces"]


def test_every_container_is_locked_down():
    for d in by_kind(render(), "Deployment"):
        pod = d["spec"]["template"]["spec"]
        assert pod["securityContext"]["runAsNonRoot"] is True
        for c in pod["containers"]:
            s = c["securityContext"]
            assert s["readOnlyRootFilesystem"] is True and s["allowPrivilegeEscalation"] is False
            assert s["capabilities"]["drop"] == ["ALL"], (d["metadata"]["name"], c["name"])


def test_components_and_services():
    docs = render(*SM)
    deps = _deployments(docs)
    assert set(deps) == {"collector", "watcher", "api", "exporter"}
    args = deps["collector"]["spec"]["template"]["spec"]["containers"][0]["args"]
    assert [args[i + 1] for i, a in enumerate(args) if a == "--namespace"] == ["predict-lab", "remediate-lab", "pattern-lab"]
    assert [c["name"] for c in deps["api"]["spec"]["template"]["spec"]["containers"]] == ["api", "dashboard"]
    assert all(s["spec"].get("type", "ClusterIP") == "ClusterIP" for s in by_kind(docs, "Service"))   # nothing exposed outside
    sm = by_kind(docs, "ServiceMonitor")[0]
    assert sm["metadata"]["labels"]["release"] == "monitoring" and sm["spec"]["endpoints"][0]["port"] == "metrics"


def test_service_monitor_only_when_prometheus_operator_exists():
    assert by_kind(render(), "ServiceMonitor") == []
    assert len(by_kind(render(*SM), "ServiceMonitor")) == 1


def test_all_digit_image_tag_stays_text():
    """Seen on 23 Nov: --set image.tag=4764173 became a number and the image name became '%!s(int64=4764173)'."""
    images = {c["image"] for d in render("--set", "image.tag=4764173") if d["kind"] in ("Deployment", "Job")
              for c in d["spec"]["template"]["spec"]["containers"]}
    assert images == {"k8s-failure-agent:4764173"}, images
    assert not any("%!" in d for d in render_text("--set", "image.tag=0000001").splitlines())


@pytest.mark.parametrize("value, message", [
    ("collecter.nodeStats=false", "additional properties 'collecter'"),      # a typo is an error, not silently ignored
    ("collector.nodeStats=maybe", "want boolean"),
    ("image.pullPolicy=sometimes", "must be one of"),
    ("database.image=postgres", "does not match pattern"),                    # no tag: the 'latest' trap
    ("database.image=postgres:latest", "'not' failed"),
    ("api.port=0", "minimum"),
    ("settings.remediationNamespace=default", "must be one of settings.namespaces"),
])
def test_bad_values_are_rejected_before_rendering(value, message):
    r = subprocess.run(["helm", "template", "demo", str(CHART), "--set", value], capture_output=True, text=True)
    assert r.returncode != 0 and message in r.stderr, r.stderr


def test_numeric_tag_is_still_accepted():
    assert render("--set", "image.tag=4764173")


def _run_test_pod_script(metrics_body):
    """Run the chart's helm-test script against local stand-ins for the API and the exporter."""
    import http.server, threading
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (b"ok\n" if self.path == "/health" else metrics_body if self.path == "/metrics" else b"")
            self.send_response(200 if body else 404); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    servers = [http.server.ThreadingHTTPServer(("127.0.0.1", 0), H) for _ in range(2)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        pod = next(d for d in render() if d["metadata"]["name"].endswith("test-endpoints"))
        script = pod["spec"]["containers"][0]["args"][0]
        script = (script.replace("demo-k8s-failure-agent-api:8000", f"127.0.0.1:{servers[0].server_address[1]}")
                        .replace("demo-k8s-failure-agent-exporter:9105", f"127.0.0.1:{servers[1].server_address[1]}"))
        return subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=30)
    finally:
        for s in servers:
            s.shutdown()


def test_helm_test_pod_passes_on_a_working_release():
    pod = next(d for d in render() if d["metadata"]["name"].endswith("test-endpoints"))
    assert pod["metadata"]["annotations"]["helm.sh/hook"] == "test"
    r = _run_test_pod_script(b"# HELP k8s_failure_incidents x\nk8s_failure_incidents{outcome=\"AVOIDED\"} 18\n")
    assert r.returncode == 0 and r.stdout.count("PASS") == 3, r.stdout


def test_helm_test_pod_fails_when_metrics_are_missing():
    r = _run_test_pod_script(b"# nothing useful here\n")
    assert r.returncode == 1 and "FAIL exporter /metrics" in r.stdout, r.stdout

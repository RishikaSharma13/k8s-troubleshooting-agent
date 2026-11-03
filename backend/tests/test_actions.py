"""Tests for the safe remediation executor, using a stand-in kubectl that records every command."""
import json
import unittest
from datetime import datetime, timezone

import pytest

from app.kubernetes.kubectl_executor import KubectlResult
from app.remediation.actions import SafeRemediationExecutor

NS, DEP = "pytest-remediate", "leak-x"


class FakeKubectl:
    """Answers reads from a small fake cluster; records every command; can be told to fail steps."""

    def __init__(self, labelled=True, limit="256Mi", fail=(), containers=1):
        self.calls, self.labelled, self.limit, self.fail, self.containers = [], labelled, limit, set(fail), containers

    def writes(self):
        return [c for c in self.calls if c[0] in ("rollout", "set") and c[1] != "status"]

    def run(self, *args, timeout=30):
        self.calls.append(list(args))
        a = list(args)
        ok = lambda out="": KubectlResult(a, True, stdout=out)
        bad = lambda msg: KubectlResult(a, False, stderr=msg, error="non-zero exit")
        if a[:2] == ["get", "namespace"]:
            labels = {"reliability-agent/remediation": "allowed"} if self.labelled else {}
            return ok(json.dumps({"metadata": {"labels": labels}}))
        if a[:2] == ["get", "deployment"]:
            res = {"limits": {"memory": self.limit}} if self.limit else {}
            containers = [{"name": f"c{i}" if i else "memload", "resources": res} for i in range(self.containers)]
            return ok(json.dumps({"metadata": {"generation": 3}, "spec": {"selector": {"matchLabels": {"app": DEP}},
                                  "template": {"metadata": {}, "spec": {"containers": containers}}}}))
        if a[:2] == ["get", "pods"]:
            return ok(json.dumps({"items": [{"metadata": {"name": DEP + "-abc"}, "status": {"phase": "Running",
                                             "containerStatuses": [{"restartCount": 7}]}}]}))
        if "--dry-run=server" in a:
            return bad("admission webhook denied") if "dry" in self.fail else ok("deployment.apps/leak-x configured (server dry run)")
        if a[:2] == ["rollout", "status"]:
            return bad("timed out waiting") if "rollout" in self.fail else ok('deployment "leak-x" successfully rolled out')
        if a[0] in ("rollout", "set"):
            return bad("forbidden") if "real" in self.fail else ok("deployment.apps/leak-x restarted")
        return bad("unexpected command")


class FakeIncident:
    def __init__(self, incident_id):
        self.id = incident_id
        self.trend_data = type("T", (), {"namespace": NS, "deployment_name": DEP})()


@pytest.mark.db
class TestSafeExecutor(unittest.TestCase):
    def setUp(self):
        from app.models.database import Execution, Incident, SessionLocal
        self.E, self.I, self.Session = Execution, Incident, SessionLocal
        self._cleanup()
        db = self.Session()
        try:
            db.add(self.I(id="pytest-inc-1", pod_name=DEP + "-abc", deployment=DEP, namespace=NS, risk_score=80,
                          risk_level="HIGH", status="APPROVED", recommended_action="RESTART_DEPLOYMENT",
                          approved_action="RESTART_DEPLOYMENT"))
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = self.Session()
        try:
            db.query(self.E).filter(self.E.incident_id.like("pytest-inc-%")).delete(synchronize_session=False)
            db.query(self.I).filter(self.I.id.like("pytest-inc-%")).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def _run(self, action, **fake):
        k = FakeKubectl(**fake)
        result = SafeRemediationExecutor(k, rollout_timeout_s=5).execute_action(FakeIncident("pytest-inc-1"), action)
        db = self.Session()
        try:
            row = db.query(self.E).filter(self.E.id == result["execution_id"]).one()
        finally:
            db.close()
        return k, result, row, json.loads(row.evidence)

    def test_restart_dry_run_first_then_recorded(self):
        k, result, row, ev = self._run("RESTART_DEPLOYMENT")
        self.assertEqual((result["status"], row.status), ("COMPLETED", "SUCCESS"))
        cmds = [" ".join(c) for c in k.calls if c[0] == "rollout" and c[1] == "restart"]
        self.assertEqual(cmds, [f"rollout restart deployment/{DEP} -n {NS} --dry-run=server",
                                f"rollout restart deployment/{DEP} -n {NS}"])                 # dry run first
        self.assertEqual((ev["before"]["pods"][0]["restarts"], "after" in ev, ev["command"]),
                         (7, True, f"kubectl rollout restart deployment/{DEP} -n {NS}"))

    def test_memory_increase_is_50_percent_of_the_real_limit(self):
        k, result, row, ev = self._run("INCREASE_MEMORY_LIMIT")
        self.assertEqual((row.status, row.old_value, row.new_value), ("SUCCESS", "256Mi", "384Mi"))
        self.assertIn(["set", "resources", f"deployment/{DEP}", "-n", NS, "-c", "memload", "--limits", "memory=384Mi"],
                      k.calls)

    def test_namespace_without_label_is_refused_and_nothing_changes(self):
        k, result, row, ev = self._run("RESTART_DEPLOYMENT", labelled=False)
        self.assertEqual((result["status"], row.status, k.writes()), ("REFUSED", "REFUSED", []))
        self.assertIn("not labelled", row.error_message)

    def test_unknown_action_refused_before_touching_the_cluster(self):
        k, result, row, ev = self._run("SCALE_DEPLOYMENT")
        self.assertEqual((row.status, k.calls), ("REFUSED", []))

    def test_failed_dry_run_means_the_real_command_never_runs(self):
        k, result, row, ev = self._run("RESTART_DEPLOYMENT", fail={"dry"})
        self.assertEqual(row.status, "REFUSED")
        self.assertEqual([c for c in k.writes() if "--dry-run=server" not in c], [])

    def test_unreadable_or_missing_limit_is_refused_never_guessed(self):
        for fake in ({"limit": None}, {"limit": "lots"}, {"containers": 2}):
            k, result, row, ev = self._run("INCREASE_MEMORY_LIMIT", **fake)
            self.assertEqual((row.status, k.writes()), ("REFUSED", []), fake)

    def test_cap_on_new_memory_limit(self):
        k, result, row, ev = self._run("INCREASE_MEMORY_LIMIT", limit="800Mi")      # 1200Mi > 1024Mi cap
        self.assertEqual((row.status, k.writes()), ("REFUSED", []))
        self.assertIn("cap", row.error_message)

    def test_failures_are_recorded_too(self):
        _, result, row, ev = self._run("RESTART_DEPLOYMENT", fail={"real"})
        self.assertEqual((result["status"], row.status), ("FAILED", "FAILED"))
        _, result, row, ev = self._run("RESTART_DEPLOYMENT", fail={"rollout"})
        self.assertEqual(row.status, "FAILED")
        self.assertIn("after", ev)                                                   # evidence kept even on failure

    def test_through_the_api_with_the_approval_gate(self):
        from fastapi.testclient import TestClient
        from app.api import predictor_routes
        from app.main import app
        original = predictor_routes.remediation_executor
        k = FakeKubectl()
        predictor_routes.remediation_executor = SafeRemediationExecutor(k, rollout_timeout_s=5)
        try:
            client = TestClient(app)
            wrong = client.post("/api/v1/remediation/execute",
                                params={"incident_id": "pytest-inc-1", "action": "INCREASE_MEMORY_LIMIT"}).json()
            self.assertIn("does not match the approved action", wrong["error"])
            self.assertEqual(k.calls, [])                                             # the gate stopped it first
            ok = client.post("/api/v1/remediation/execute",
                             params={"incident_id": "pytest-inc-1", "action": "RESTART_DEPLOYMENT"}).json()
            self.assertEqual((ok["status"], ok["new_status"]), ("success", "EXECUTING"))
        finally:
            predictor_routes.remediation_executor = original

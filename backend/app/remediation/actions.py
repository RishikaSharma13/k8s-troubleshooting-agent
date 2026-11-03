"""Safe execution of an approved remediation action, with recorded evidence.

Used by POST /remediation/execute after its approval gate (the incident must be APPROVED,
and the action must equal the approved action). This module adds the cluster-side checks:

1. Only RESTART_DEPLOYMENT and INCREASE_MEMORY_LIMIT can run.
2. The namespace must carry the label reliability-agent/remediation=allowed, read live from
   the cluster (so predict-lab, the evaluation's ground truth, can never be changed).
3. A "before" snapshot is taken: memory limits, pods, restart counts.
4. The exact command is built. INCREASE_MEMORY_LIMIT raises each container's limit by 50% of
   the limit actually read, capped at 1 GiB; if a limit cannot be read the action is refused
   (never guessed).
5. The same command runs first as a server-side dry run; if that fails, nothing changes.
   (A restart is done as `kubectl patch` of the restartedAt annotation, which is exactly what
   `kubectl rollout restart` does, because `rollout restart` has no --dry-run flag.)
6. The command runs, the rollout is awaited, an "after" snapshot is taken.
7. Every attempt is stored in `executions` (SUCCESS, FAILED or REFUSED) with the evidence.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from loguru import logger

from app.collector.memory_collector import parse_quantity_bytes
from app.kubernetes.kubectl_executor import KubectlExecutor
from app.models.database import Execution, SessionLocal

ALLOWED_ACTIONS = ("RESTART_DEPLOYMENT", "INCREASE_MEMORY_LIMIT")
ALLOW_LABEL_KEY, ALLOW_LABEL_VALUE = "reliability-agent/remediation", "allowed"
MEMORY_FACTOR = 1.5
MEMORY_CAP_BYTES = 1024 * 1024 * 1024
MiB = 1024 * 1024


class Refused(Exception):
    """A safety check failed; nothing was changed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SafeRemediationExecutor:
    def __init__(self, kubectl: Optional[KubectlExecutor] = None, session_factory: Callable = SessionLocal,
                 rollout_timeout_s: int = 120):
        self.kubectl = kubectl or KubectlExecutor()
        self.session_factory = session_factory
        self.rollout_timeout_s = rollout_timeout_s

    # ---------------- cluster reads ----------------
    def _json(self, *args: str) -> dict:
        r = self.kubectl.run(*args)
        if not r.success:
            raise Refused(f"could not read {' '.join(args[:3])}: {(r.stderr or r.error or '').strip()[:200]}")
        return r.json()

    def check_namespace(self, namespace: str) -> None:
        labels = self._json("get", "namespace", namespace, "-o", "json").get("metadata", {}).get("labels", {}) or {}
        if labels.get(ALLOW_LABEL_KEY) != ALLOW_LABEL_VALUE:
            raise Refused(f"namespace {namespace} is not labelled {ALLOW_LABEL_KEY}={ALLOW_LABEL_VALUE}")

    def snapshot(self, namespace: str, deployment: str) -> dict[str, Any]:
        dep = self._json("get", "deployment", deployment, "-n", namespace, "-o", "json")
        spec = dep.get("spec", {})
        selector = ",".join(f"{k}={v}" for k, v in (spec.get("selector", {}).get("matchLabels") or {}).items())
        if not selector:
            raise Refused(f"deployment {deployment} has no label selector")
        pods = self._json("get", "pods", "-n", namespace, "-l", selector, "-o", "json")
        return {
            "taken_at": _now(),
            "generation": dep.get("metadata", {}).get("generation"),
            "restarted_at": (spec.get("template", {}).get("metadata", {}).get("annotations") or {})
                            .get("kubectl.kubernetes.io/restartedAt"),
            "memory_limits": {c["name"]: (c.get("resources", {}).get("limits") or {}).get("memory")
                              for c in spec.get("template", {}).get("spec", {}).get("containers", [])},
            "pods": [{"name": p["metadata"]["name"], "phase": p.get("status", {}).get("phase"),
                      "restarts": sum(cs.get("restartCount", 0) for cs in p.get("status", {}).get("containerStatuses", []) or [])}
                     for p in pods.get("items", [])],
        }

    # ---------------- the command ----------------
    @staticmethod
    def build_command(action: str, namespace: str, deployment: str, before: dict) -> tuple[list[str], str, str]:
        """(kubectl args, old value, new value) for the action. Raises Refused rather than guessing."""
        if action == "RESTART_DEPLOYMENT":
            # What `kubectl rollout restart` does, as a patch, because only `patch` supports --dry-run=server
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            body = json.dumps({"spec": {"template": {"metadata": {"annotations": {
                "kubectl.kubernetes.io/restartedAt": stamp}}}}}, separators=(",", ":"))
            return (["patch", f"deployment/{deployment}", "-n", namespace, "--type=strategic", "-p", body],
                    ",".join(p["name"] for p in before["pods"]) or "-", f"new pods (restartedAt {stamp})")
        if action == "INCREASE_MEMORY_LIMIT":
            limits = before["memory_limits"]
            if not limits or any(not v for v in limits.values()):
                raise Refused(f"cannot raise memory: some containers have no memory limit ({limits})")
            if len(limits) != 1:
                raise Refused(f"cannot raise memory safely for more than one container ({list(limits)})")
            (container, raw), = limits.items()
            try:
                old_bytes = parse_quantity_bytes(raw)
            except Exception:
                raise Refused(f"cannot read memory limit {raw!r}")
            new_mib = math.ceil(old_bytes * MEMORY_FACTOR / MiB)
            if new_mib * MiB > MEMORY_CAP_BYTES:
                raise Refused(f"new limit {new_mib}Mi would exceed the {MEMORY_CAP_BYTES // MiB}Mi cap")
            return (["set", "resources", f"deployment/{deployment}", "-n", namespace, "-c", container,
                     "--limits", f"memory={new_mib}Mi"], raw, f"{new_mib}Mi")
        raise Refused(f"action {action} is not executable (allowed: {', '.join(ALLOWED_ACTIONS)})")

    # ---------------- execution ----------------
    def execute_action(self, incident, action_type: str) -> dict:
        ns, dep = incident.trend_data.namespace, incident.trend_data.deployment_name
        execution_id = str(uuid.uuid4())
        evidence: dict[str, Any] = {"namespace": ns, "deployment": dep, "action": action_type, "started_at": _now()}
        status, error, old_value, new_value = "FAILED", None, None, None
        try:
            if action_type not in ALLOWED_ACTIONS:
                raise Refused(f"action {action_type} is not executable (allowed: {', '.join(ALLOWED_ACTIONS)})")
            self.check_namespace(ns)
            evidence["before"] = before = self.snapshot(ns, dep)
            args, old_value, new_value = self.build_command(action_type, ns, dep, before)
            evidence["command"] = "kubectl " + " ".join(args)

            dry = self.kubectl.run(*args, "--dry-run=server")
            evidence["dry_run"] = (dry.stdout or dry.stderr or dry.error or "").strip()[:500]
            if not dry.success:
                raise Refused(f"server-side dry run failed: {evidence['dry_run']}")

            real = self.kubectl.run(*args)
            evidence["output"] = (real.stdout or real.stderr or real.error or "").strip()[:500]
            if not real.success:
                raise RuntimeError(f"command failed: {evidence['output']}")

            rollout = self.kubectl.run("rollout", "status", f"deployment/{dep}", "-n", ns,
                                       f"--timeout={self.rollout_timeout_s}s", timeout=self.rollout_timeout_s + 15)
            evidence["rollout"] = (rollout.stdout or rollout.stderr or rollout.error or "").strip()[-300:]
            evidence["after"] = self.snapshot(ns, dep)
            if not rollout.success:
                raise RuntimeError(f"rollout did not complete within {self.rollout_timeout_s}s")
            status = "SUCCESS"
        except Refused as exc:
            status, error = "REFUSED", str(exc)
        except Exception as exc:
            status, error = "FAILED", str(exc)
        evidence["finished_at"] = _now()
        if error:
            evidence["error"] = error
        self._record(execution_id, incident.id, action_type, status, old_value, new_value, error, evidence)
        log = logger.info if status == "SUCCESS" else logger.warning
        log("Execution {} {} on {}/{}: {}{}", execution_id, action_type, ns, dep, status, f" ({error})" if error else "")
        return {"execution_id": execution_id, "action": action_type, "namespace": ns, "deployment": dep,
                # COMPLETED = done and awaiting verification; the API maps anything else to a FAILED incident
                "status": "COMPLETED" if status == "SUCCESS" else status,
                "old_value": old_value, "new_value": new_value, "error": error, "evidence": evidence}

    def _record(self, execution_id, incident_id, action, status, old_value, new_value, error, evidence) -> None:
        db = self.session_factory()
        try:
            db.add(Execution(id=execution_id, incident_id=incident_id, action=action, status=status,
                             old_value=(old_value or "")[:255] or None, new_value=(new_value or "")[:255] or None,
                             error_message=(error or "")[:500] or None, evidence=json.dumps(evidence)))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not record execution {}", execution_id)
            raise
        finally:
            db.close()

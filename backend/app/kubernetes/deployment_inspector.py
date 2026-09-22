from typing import Any

from .kubectl_executor import KubectlExecutor


class DeploymentInspector:
    def __init__(self, kubectl: KubectlExecutor) -> None:
        self.kubectl = kubectl

    def inspect(self) -> dict[str, Any]:
        result = self.kubectl.run("get", "deployments", "-A", "-o", "json")
        if not result.success:
            return {"deployments": [], "healthy": False, "error": result.error, "details": result.stderr.strip()}
        deployments = []
        for item in (result.json() or {}).get("items", []):
            spec, status = item.get("spec", {}), item.get("status", {})
            desired = spec.get("replicas", 0)
            available = status.get("availableReplicas", 0)
            unavailable = status.get("unavailableReplicas", 0)
            conditions = [{"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason"), "message": c.get("message")} for c in status.get("conditions", [])]
            deployments.append({"name": item["metadata"].get("name"), "namespace": item["metadata"].get("namespace"), "desired_replicas": desired, "available_replicas": available, "unavailable_replicas": unavailable, "healthy": available >= desired and unavailable == 0, "conditions": conditions})
        return {"healthy": all(item["healthy"] for item in deployments), "deployments": deployments}

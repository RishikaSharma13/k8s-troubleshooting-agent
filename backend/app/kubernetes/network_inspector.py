from typing import Any

from .kubectl_executor import KubectlExecutor


class NetworkInspector:
    def __init__(self, kubectl: KubectlExecutor) -> None:
        self.kubectl = kubectl

    def inspect(self) -> dict[str, Any]:
        services = self.kubectl.run("get", "services", "-A", "-o", "json")
        endpoints = self.kubectl.run("get", "endpoints", "-A", "-o", "json")
        if not services.success or not endpoints.success:
            return {"services": [], "healthy": False, "error": services.error or endpoints.error, "details": (services.stderr or endpoints.stderr).strip()}
        endpoint_names = {item.get("metadata", {}).get("name") for item in (endpoints.json() or {}).get("items", []) if item.get("subsets")}
        findings, summary = [], []
        for item in (services.json() or {}).get("items", []):
            name, namespace = item["metadata"].get("name"), item["metadata"].get("namespace")
            if item.get("spec", {}).get("type") == "ExternalName":
                continue
            has_endpoints = name in endpoint_names
            service = {"name": name, "namespace": namespace, "selector": item.get("spec", {}).get("selector", {}), "has_endpoints": has_endpoints, "healthy": has_endpoints}
            summary.append(service)
            if not has_endpoints:
                findings.append({"name": name, "namespace": namespace, "issue": "Missing endpoints or selector mismatch"})
        return {"healthy": not findings, "services": summary, "findings": findings}

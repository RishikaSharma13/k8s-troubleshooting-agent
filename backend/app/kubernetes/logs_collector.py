from typing import Any

from .kubectl_executor import KubectlExecutor


class LogsCollector:
    def __init__(self, kubectl: KubectlExecutor, max_lines: int = 80) -> None:
        self.kubectl = kubectl
        self.max_lines = max_lines

    def collect(self, pods: dict[str, Any]) -> list[dict[str, Any]]:
        findings = []
        for pod in pods.get("problematic_pods", []):
            result = self.kubectl.run("logs", pod["name"], "-n", pod["namespace"], "--tail", str(self.max_lines))
            findings.append({"name": pod["name"], "namespace": pod["namespace"], "success": result.success, "logs": result.stdout[-12000:], "error": result.error or result.stderr.strip() or None})
        return findings

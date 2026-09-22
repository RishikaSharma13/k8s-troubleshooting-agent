from typing import Any

from .kubectl_executor import KubectlExecutor

PROBLEM_STATUSES = {"CrashLoopBackOff", "ImagePullBackOff", "Pending", "Error", "OOMKilled", "ContainerCreating"}


class PodInspector:
    def __init__(self, kubectl: KubectlExecutor) -> None:
        self.kubectl = kubectl

    def inspect(self) -> dict[str, Any]:
        result = self.kubectl.run("get", "pods", "-A", "-o", "json")
        if not result.success:
            return {"healthy": False, "problematic_pods": [], "error": result.error, "details": result.stderr.strip()}
        pods = result.json() or {}
        problematic = []
        for item in pods.get("items", []):
            status = item.get("status", {})
            phase = status.get("phase", "Unknown")
            reasons = []
            for container in status.get("initContainerStatuses", []) + status.get("containerStatuses", []):
                state = container.get("state", {})
                waiting = state.get("waiting", {})
                terminated = state.get("terminated", {})
                reasons.extend(filter(None, [waiting.get("reason"), terminated.get("reason")]))
            detected = next((reason for reason in reasons if reason in PROBLEM_STATUSES), None)
            if detected is None and phase in PROBLEM_STATUSES:
                detected = phase
            if detected:
                problematic.append({"name": item["metadata"].get("name"), "namespace": item["metadata"].get("namespace"), "status": detected})
        return {"healthy": not problematic, "problematic_pods": problematic}

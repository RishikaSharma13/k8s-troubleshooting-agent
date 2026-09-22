from typing import Any

from .kubectl_executor import KubectlExecutor

EVENT_REASONS = {"FailedScheduling", "BackOff", "FailedMount", "FailedPull", "ErrImagePull", "Unhealthy"}


class EventsAnalyzer:
    def __init__(self, kubectl: KubectlExecutor) -> None:
        self.kubectl = kubectl

    def analyze(self) -> dict[str, Any]:
        result = self.kubectl.run("get", "events", "-A", "-o", "json")
        if not result.success:
            return {"findings": [], "error": result.error, "details": result.stderr.strip()}
        findings = []
        for event in (result.json() or {}).get("items", []):
            reason = event.get("reason")
            if reason in EVENT_REASONS:
                findings.append({"reason": reason, "namespace": event.get("metadata", {}).get("namespace"), "object": event.get("involvedObject", {}).get("name"), "message": event.get("message", "")})
        return {"findings": findings, "healthy": not findings}

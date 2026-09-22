from typing import Any

from app.core.config import settings
from app.kubernetes.deployment_inspector import DeploymentInspector
from app.kubernetes.events_analyzer import EventsAnalyzer
from app.kubernetes.kubectl_executor import KubectlExecutor
from app.kubernetes.logs_collector import LogsCollector
from app.kubernetes.network_inspector import NetworkInspector
from app.kubernetes.pod_inspector import PodInspector


class InvestigationService:
    def __init__(self) -> None:
        kubectl = KubectlExecutor(settings.kubeconfig_path)
        self.pods = PodInspector(kubectl)
        self.logs = LogsCollector(kubectl)
        self.events = EventsAnalyzer(kubectl)
        self.deployments = DeploymentInspector(kubectl)
        self.network = NetworkInspector(kubectl)

    def investigate(self) -> dict[str, Any]:
        pods = self.pods.inspect()
        return {"pods": pods, "logs": {"findings": self.logs.collect(pods)}, "events": self.events.analyze(), "deployments": self.deployments.inspect(), "network": self.network.inspect()}

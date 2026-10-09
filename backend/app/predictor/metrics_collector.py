from typing import List, Dict, Optional
from datetime import datetime
from loguru import logger

from app.kubernetes.kubectl_executor import KubectlExecutor
from app.models.database import SessionLocal, MetricsHistory
from app.models.risk import Metric, TrendData
from app.predictor.trend_detector import TrendDetector
from app.predictor.risk_manager import RiskManager


class MetricsCollector:
    """Collects metrics from Kubernetes cluster and tracks trends"""

    def __init__(self, kubeconfig_path: str = None, context: str = None):
        """Initialize metrics collector with kubectl"""
        self.kubectl = KubectlExecutor(
            kubeconfig_path=kubeconfig_path or "",
            context=context,
        )
        self.risk_manager = RiskManager()
        self.trend_detector = TrendDetector()
        logger.info("MetricsCollector initialized")

    def scan_namespace(self, namespace: str = "default") -> Dict:
        """
        Scan a namespace for pods with rising resource usage.

        Returns: Dictionary containing risk information.
        """
        try:
            logger.info(f"Scanning namespace: {namespace}")

            # Get all pods in namespace
            pods = self._get_pods_in_namespace(namespace)

            if not pods:
                logger.warning(f"No pods found in {namespace}")
                return {
                    "status": "success",
                    "namespace": namespace,
                    "pods_scanned": 0,
                    "risks": [],
                }

            risks = []

            # Get current CPU and memory usage from Metrics Server
            pod_metrics = self._get_pod_metrics(namespace)

            # Process each pod
            for pod in pods:
                pod_name = pod["name"]
                deployment_name = pod.get("deployment", "unknown")
                restart_count = pod.get("restart_count", 0)

                # Prefer real Metrics Server memory utilization.
                # Fall back to the existing restart-based estimate when
                # metrics are unavailable or no memory limit is defined.
                memory_percent = self._get_memory_percent(
                    pod=pod,
                    pod_metrics=pod_metrics,
                    restart_count=restart_count,
                )

                # Update metrics in risk manager
                trend_data = self.risk_manager.update_pod_metrics(
                    namespace=namespace,
                    pod_name=pod_name,
                    deployment_name=deployment_name,
                    memory_percent=memory_percent,
                    restart_count=restart_count,
                )

                if trend_data:
                    # Calculate trend
                    memory_values = [
                        m.value for m in trend_data.metrics_history
                    ]
                    slope, direction = self.trend_detector.calculate_trend(
                        memory_values
                    )

                    # Calculate risk score
                    risk_score = self.trend_detector.calculate_risk_score(
                        current_value=memory_percent,
                        trend_slope=slope,
                        restart_count=restart_count,
                    )

                    # Update trend data
                    trend_data.risk_score = risk_score
                    trend_data.risk_level = (
                        self.trend_detector.get_risk_level(risk_score)
                    )

                    # Add to risks if score is significant
                    if risk_score >= 20:
                        pending_incident = (
                            self.risk_manager.get_pending_incident_for_pod(
                                namespace=namespace,
                                pod_name=pod_name,
                            )
                        )

                        if pending_incident is None:
                            incident = self.create_incident_for_pod(
                                namespace=namespace,
                                pod_name=pod_name,
                                deployment=deployment_name,
                            )

                            if incident:
                                logger.info(
                                    "Created pending incident {} for {}/{}",
                                    incident["incident_id"],
                                    namespace,
                                    pod_name,
                                )

                        risks.append({
                            "pod_name": pod_name,
                            "deployment": deployment_name,
                            "namespace": namespace,
                            "memory_percent": round(memory_percent, 2),
                            "restart_count": restart_count,
                            "trend_slope": round(slope, 2),
                            "trend_direction": direction,
                            "risk_score": round(risk_score, 2),
                            "risk_level": trend_data.risk_level,
                            "metrics_history_count": len(memory_values),
                        })

                    logger.info(
                        f"Pod {pod_name}: Memory={memory_percent:.1f}%, "
                        f"Restarts={restart_count}, "
                        f"Risk={risk_score:.1f}/100 "
                        f"({trend_data.risk_level})"
                    )

            return {
                "status": "success",
                "namespace": namespace,
                "pods_scanned": len(pods),
                "risks": sorted(
                    risks,
                    key=lambda x: x["risk_score"],
                    reverse=True,
                ),
            }

        except Exception as e:
            logger.error(f"Error scanning namespace {namespace}: {e}")
            return {
                "status": "error",
                "namespace": namespace,
                "error": str(e),
                "risks": [],
            }

    def get_pods_in_namespace(
        self,
        namespace: str = "default",
    ) -> List[Dict]:
        """Get all pods in a namespace"""
        return self._get_pods_in_namespace(namespace)

    def _get_pods_in_namespace(
        self,
        namespace: str,
    ) -> List[Dict]:
        """
        Query Kubernetes API for pods in namespace.

        Returns: List of pod info dicts.
        """
        try:
            result = self.kubectl.run(
                "get",
                "pods",
                "-n",
                namespace,
                "-o",
                "json",
            )

            if not result.success:
                logger.error(f"kubectl failed: {result.error}")
                return []

            pods_data = result.json()

            if not pods_data or "items" not in pods_data:
                return []

            pods = []

            for item in pods_data.get("items", []):
                metadata = item.get("metadata", {})
                status = item.get("status", {})

                # Get restart count from container statuses
                restart_count = 0
                container_statuses = status.get(
                    "containerStatuses",
                    [],
                )

                if container_statuses:
                    restart_count = container_statuses[0].get(
                        "restartCount",
                        0,
                    )

                # Get deployment from labels
                labels = metadata.get("labels", {})
                deployment = labels.get(
                    "app",
                    labels.get("deployment", "unknown"),
                )

                # Get memory limit from the first container.
                # This is used with Metrics Server usage to calculate
                # actual memory utilization percentage.
                memory_limit = None
                containers = item.get("spec", {}).get(
                    "containers",
                    [],
                )

                if containers:
                    resources = containers[0].get(
                        "resources",
                        {},
                    )
                    limits = resources.get("limits", {})
                    memory_limit = limits.get("memory")

                pods.append({
                    "name": metadata.get("name"),
                    "namespace": metadata.get("namespace"),
                    "deployment": deployment,
                    "status": status.get("phase", "Unknown"),
                    "restart_count": restart_count,
                    "memory_limit": memory_limit,
                })

            logger.debug(
                f"Found {len(pods)} pods in {namespace}"
            )
            return pods

        except Exception as e:
            logger.error(
                f"Error getting pods in {namespace}: {e}"
            )
            return []

    def _get_pod_metrics(
        self,
        namespace: str,
    ) -> Dict[str, Dict[str, float]]:
        """
        Get current CPU and memory usage from Kubernetes Metrics Server.

        Returns:
            {
                "pod-name": {
                    "cpu_mi": 97.0,
                    "memory_mi": 180.0
                }
            }

        Returns an empty dictionary when Metrics Server is unavailable.
        """
        try:
            result = self.kubectl.run(
                "top",
                "pods",
                "-n",
                namespace,
                "--no-headers",
            )

            if not result.success:
                logger.warning(
                    f"Metrics Server unavailable in {namespace}: "
                    f"{result.error}"
                )
                return {}

            metrics = {}

            for line in result.stdout.strip().splitlines():
                parts = line.split()

                if len(parts) < 3:
                    continue

                pod_name = parts[0]
                cpu_value = parts[1]
                memory_value = parts[2]

                metrics[pod_name] = {
                    "cpu_mi": self._parse_cpu_to_millicores(
                        cpu_value
                    ),
                    "memory_mi": self._parse_memory_to_mi(
                        memory_value
                    ),
                }

            logger.debug(
                f"Collected Metrics Server data for "
                f"{len(metrics)} pods in {namespace}"
            )

            return metrics

        except Exception as e:
            logger.warning(
                f"Error collecting pod metrics: {e}"
            )
            return {}

    def _parse_cpu_to_millicores(
        self,
        value: str,
    ) -> float:
        """Convert Kubernetes CPU quantity to millicores."""
        value = value.strip()

        if value.endswith("n"):
            return float(value[:-1]) / 1_000_000

        if value.endswith("u"):
            return float(value[:-1]) / 1_000

        if value.endswith("m"):
            return float(value[:-1])

        return float(value) * 1000

    def _parse_memory_to_mi(
        self,
        value: str,
    ) -> float:
        """Convert Kubernetes memory quantity to Mi."""
        value = value.strip()

        if value.endswith("Ki"):
            return float(value[:-2]) / 1024

        if value.endswith("Mi"):
            return float(value[:-2])

        if value.endswith("Gi"):
            return float(value[:-2]) * 1024

        if value.endswith("Ti"):
            return float(value[:-2]) * 1024 * 1024

        if value.endswith("K"):
            return float(value[:-1]) / 1024

        if value.endswith("M"):
            return float(value[:-1])

        if value.endswith("G"):
            return float(value[:-1]) * 1024

        # Kubernetes may return raw bytes.
        return float(value) / (1024 * 1024)

    def _get_memory_percent(
        self,
        pod: Dict,
        pod_metrics: Dict[str, Dict[str, float]],
        restart_count: int,
    ) -> float:
        """
        Calculate memory utilization from actual Metrics Server data.

        Falls back to the existing restart-based estimate when actual
        usage or a memory limit is unavailable.
        """
        pod_name = pod.get("name")
        memory_limit = pod.get("memory_limit")

        metric = pod_metrics.get(pod_name)

        if metric and memory_limit:
            try:
                limit_mi = self._parse_memory_to_mi(
                    memory_limit
                )
                usage_mi = metric["memory_mi"]

                if limit_mi > 0:
                    memory_percent = (
                        usage_mi / limit_mi
                    ) * 100

                    logger.debug(
                        f"Pod {pod_name}: actual memory usage "
                        f"{usage_mi:.1f}Mi / {limit_mi:.1f}Mi "
                        f"({memory_percent:.1f}%)"
                    )

                    return min(
                        memory_percent,
                        100.0,
                    )

            except (
                ValueError,
                KeyError,
                TypeError,
            ) as e:
                logger.warning(
                    f"Could not calculate memory utilization "
                    f"for {pod_name}: {e}"
                )

        # Metrics unavailable or no memory limit.
        fallback = self._estimate_memory_percent(
            restart_count
        )

        logger.debug(
            f"Pod {pod_name}: using restart-based "
            f"memory estimate {fallback:.1f}%"
        )

        return fallback

    def _estimate_memory_percent(
        self,
        restart_count: int,
    ) -> float:
        """
        Estimate memory usage percentage based on restart count.

        Note: For Phase 2, this will use metrics-server for actual memory data.
        For now, we estimate based on pod behavior.
        """
        base_memory = 45.0

        # More restarts suggest memory issues
        if restart_count > 5:
            return min(base_memory + 30, 100.0)
        elif restart_count > 3:
            return min(base_memory + 15, 100.0)
        elif restart_count > 1:
            return min(base_memory + 8, 100.0)
        else:
            return base_memory

    def get_pending_incidents(
        self,
    ) -> List[Dict]:
        """Get all pending risk incidents"""
        incidents = self.risk_manager.get_pending_incidents()

        return [
            {
                "incident_id": incident.id,
                "pod_name": incident.trend_data.pod_name,
                "deployment": incident.trend_data.deployment_name,
                "namespace": incident.trend_data.namespace,
                "risk_score": round(
                    incident.trend_data.risk_score,
                    2,
                ),
                "risk_level": incident.trend_data.risk_level,
                "recommended_action": incident.recommended_action,
                "confidence": round(
                    incident.confidence,
                    2,
                ),
                "status": incident.status,
                "created_at": incident.created_at.isoformat(),
            }
            for incident in incidents
        ]

    def create_incident_for_pod(
        self,
        namespace: str,
        pod_name: str,
        deployment: str,
    ) -> Optional[Dict]:
        """Create a risk incident for a pod"""
        try:
            key = f"{namespace}/{pod_name}"

            if key not in self.risk_manager.trends:
                logger.warning(f"No trend data for {key}")
                return None

            trend_data = self.risk_manager.trends[key]

            # Determine recommended action based on risk level
            if trend_data.risk_level == "CRITICAL":
                action = "SCALE_DEPLOYMENT"
                confidence = 85.0
            elif trend_data.risk_level == "HIGH":
                action = "SCALE_DEPLOYMENT"
                confidence = 75.0
            else:
                action = "INVESTIGATE_LOGS"
                confidence = 60.0

            # Create incident
            incident = self.risk_manager.create_incident(
                trend_data=trend_data,
                recommended_action=action,
                confidence=confidence,
            )

            return {
                "incident_id": incident.id,
                "pod_name": pod_name,
                "deployment": deployment,
                "namespace": namespace,
                "recommended_action": action,
                "confidence": confidence,
            }

        except Exception as e:
            logger.error(
                f"Error creating incident for {pod_name}: {e}"
            )
            return None

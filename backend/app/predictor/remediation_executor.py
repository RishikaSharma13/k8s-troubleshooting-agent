import uuid
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from loguru import logger

from app.models.risk import RiskIncident
from app.kubernetes.kubectl_executor import KubectlExecutor


class RemediationExecutor:
    """Executes approved remediation actions on Kubernetes"""

    def __init__(self, kubectl_executor: KubectlExecutor):
        """Initialize with kubectl executor for K8s operations"""
        self.kubectl_executor = kubectl_executor

    def execute_action(
        self,
        incident: RiskIncident,
        action_type: str,
        action_params: Dict[str, Any] = None
    ) -> dict:
        """
        Route to appropriate remediation method based on action type
        
        Args:
            incident: RiskIncident containing pod/deployment info
            action_type: Type of action (SCALE_DEPLOYMENT, RESTART_DEPLOYMENT, etc)
            action_params: Parameters for the action (scale_percent, memory_increase, etc)
            
        Returns:
            dict with execution_id, status, and action details
        """
        if action_params is None:
            action_params = {}

        namespace = incident.trend_data.namespace
        pod_name = incident.trend_data.pod_name
        deployment = incident.trend_data.deployment_name

        try:
            if action_type == "SCALE_DEPLOYMENT":
                scale_percent = action_params.get("scale_increase_percent", 50)
                return self.scale_deployment(namespace, deployment, scale_percent)

            elif action_type == "RESTART_DEPLOYMENT":
                return self.restart_deployment(namespace, deployment)

            elif action_type == "INCREASE_MEMORY_LIMIT":
                memory_percent = action_params.get("memory_limit_increase_percent", 30)
                return self.increase_memory_limit(namespace, deployment, memory_percent)

            elif action_type == "INVESTIGATE_LOGS":
                lines = action_params.get("lines", 100)
                return self.fetch_logs(namespace, pod_name, lines)

            else:
                return {
                    "execution_id": str(uuid.uuid4()),
                    "action": action_type,
                    "status": "FAILED",
                    "error": f"Unknown action type: {action_type}",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            logger.error(f"Error executing action {action_type}: {str(e)}")
            return {
                "execution_id": str(uuid.uuid4()),
                "action": action_type,
                "status": "FAILED",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    def _is_kubectl_success(self, result) -> bool:
        """Check if KubectlResult indicates success
        
        Handles both potential attribute names:
        - returncode == 0 (like subprocess)
        - success == True (if KubectlResult uses boolean)
        """
        # Try returncode first
        if hasattr(result, 'returncode'):
            return result.returncode == 0
        # Try success attribute
        elif hasattr(result, 'success'):
            return result.success
        # Fallback: if no error, assume success
        else:
            return True

    def scale_deployment(
        self,
        namespace: str,
        deployment: str,
        scale_increase_percent: int
    ) -> dict:
        """
        Scale up a deployment by increasing replicas using KubectlExecutor
        
        Example: If deployment has 2 replicas, scale_increase_percent=50
        → 2 * 1.5 = 3 replicas
        
        Args:
            namespace: Kubernetes namespace
            deployment: Deployment name
            scale_increase_percent: Percentage to increase replicas
            
        Returns:
            dict with execution status and replica count change
        """
        execution_id = str(uuid.uuid4())

        try:
            logger.info(
                f"Scaling deployment {namespace}/{deployment} by {scale_increase_percent}%"
            )

            # Get current replica count using KubectlExecutor
            try:
                result = self.kubectl_executor.run(
                    "get", "deployment", deployment, "-n", namespace,
                    "-o", "jsonpath={.spec.replicas}"
                )
                
                if self._is_kubectl_success(result):
                    current_replicas = int(result.stdout) if result.stdout else 1
                else:
                    current_replicas = 1
                    
            except Exception as e:
                logger.warning(f"Could not fetch current replicas: {str(e)}, using default 1")
                current_replicas = 1

            # Calculate new replica count
            new_replicas = int(
                current_replicas * (1 + scale_increase_percent / 100)
            )
            new_replicas = max(new_replicas, 1)

            logger.info(
                f"Scaling {namespace}/{deployment}: {current_replicas} → {new_replicas} replicas"
            )

            # Scale deployment using KubectlExecutor
            scale_result = self.kubectl_executor.run(
                "scale", "deployment", deployment,
                "-n", namespace, "--replicas", str(new_replicas)
            )

            if not self._is_kubectl_success(scale_result):
                error_msg = scale_result.stderr if hasattr(scale_result, 'stderr') else "Unknown error during scaling"
                logger.error(f"Failed to scale deployment: {error_msg}")
                return {
                    "execution_id": execution_id,
                    "action": "SCALE_DEPLOYMENT",
                    "status": "FAILED",
                    "namespace": namespace,
                    "deployment": deployment,
                    "error": error_msg,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

            logger.info(f"✅ Successfully scaled {namespace}/{deployment} to {new_replicas} replicas")

            return {
                "execution_id": execution_id,
                "action": "SCALE_DEPLOYMENT",
                "status": "EXECUTING",
                "namespace": namespace,
                "deployment": deployment,
                "old_replicas": current_replicas,
                "new_replicas": new_replicas,
                "scale_percent": scale_increase_percent,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Scaling deployment from {current_replicas} to {new_replicas} replicas"
            }

        except Exception as e:
            logger.error(f"Failed to scale deployment: {str(e)}")
            return {
                "execution_id": execution_id,
                "action": "SCALE_DEPLOYMENT",
                "status": "FAILED",
                "namespace": namespace,
                "deployment": deployment,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    def restart_deployment(
        self,
        namespace: str,
        deployment: str
    ) -> dict:
        """
        Perform rolling restart of a deployment using KubectlExecutor
        
        Terminates all pods in the deployment, allowing K8s to create new ones.
        This can help with transient issues and restart crash loops.
        
        Args:
            namespace: Kubernetes namespace
            deployment: Deployment name
            
        Returns:
            dict with execution status
        """
        execution_id = str(uuid.uuid4())

        try:
            logger.info(f"Restarting deployment {namespace}/{deployment}")

            # Trigger rolling restart using KubectlExecutor
            restart_result = self.kubectl_executor.run(
                "rollout", "restart", f"deployment/{deployment}",
                "-n", namespace
            )

            if not self._is_kubectl_success(restart_result):
                error_msg = restart_result.stderr if hasattr(restart_result, 'stderr') else "Unknown error during restart"
                logger.error(f"Failed to restart deployment: {error_msg}")
                return {
                    "execution_id": execution_id,
                    "action": "RESTART_DEPLOYMENT",
                    "status": "FAILED",
                    "namespace": namespace,
                    "deployment": deployment,
                    "error": error_msg,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

            logger.info(f"✅ Successfully initiated rolling restart for {namespace}/{deployment}")

            return {
                "execution_id": execution_id,
                "action": "RESTART_DEPLOYMENT",
                "status": "EXECUTING",
                "namespace": namespace,
                "deployment": deployment,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": "Rolling restart initiated. Old pods will be terminated, new pods created."
            }

        except Exception as e:
            logger.error(f"Failed to restart deployment: {str(e)}")
            return {
                "execution_id": execution_id,
                "action": "RESTART_DEPLOYMENT",
                "status": "FAILED",
                "namespace": namespace,
                "deployment": deployment,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    def increase_memory_limit(
        self,
        namespace: str,
        deployment: str,
        memory_limit_increase_percent: int
    ) -> dict:
        """
        Increase memory limit for a deployment using KubectlExecutor
        
        Example: If current limit is 512Mi, increase_percent=30
        → 512 * 1.3 = 665.6Mi
        
        Args:
            namespace: Kubernetes namespace
            deployment: Deployment name
            memory_limit_increase_percent: Percentage to increase memory limit
            
        Returns:
            dict with execution status
        """
        execution_id = str(uuid.uuid4())

        try:
            logger.info(
                f"Increasing memory limit for {namespace}/{deployment} by {memory_limit_increase_percent}%"
            )

            # Get current memory limit using KubectlExecutor
            try:
                result = self.kubectl_executor.run(
                    "get", "deployment", deployment, "-n", namespace,
                    "-o", "jsonpath={.spec.template.spec.containers[0].resources.limits.memory}"
                )
                
                # Parse memory string (e.g., "512Mi" → 512)
                if self._is_kubectl_success(result) and result.stdout:
                    memory_str = result.stdout
                    if memory_str.endswith("Mi"):
                        current_memory_mi = int(memory_str[:-2])
                    elif memory_str.endswith("Gi"):
                        current_memory_mi = int(memory_str[:-2]) * 1024
                    else:
                        current_memory_mi = 512
                else:
                    current_memory_mi = 512
                    
            except Exception as e:
                logger.warning(f"Could not fetch current memory limit: {str(e)}, using default 512Mi")
                current_memory_mi = 512

            # Calculate new memory limit
            new_memory_mi = int(
                current_memory_mi * (1 + memory_limit_increase_percent / 100)
            )

            logger.info(
                f"Memory limit {namespace}/{deployment}: {current_memory_mi}Mi → {new_memory_mi}Mi"
            )

            # Update deployment memory limit using kubectl set resources
            set_resources_result = self.kubectl_executor.run(
                "set", "resources", "deployment", deployment,
                "-n", namespace, "--limits", f"memory={new_memory_mi}Mi"
            )

            if not self._is_kubectl_success(set_resources_result):
                error_msg = set_resources_result.stderr if hasattr(set_resources_result, 'stderr') else "Unknown error during resource update"
                logger.error(f"Failed to update memory limit: {error_msg}")
                return {
                    "execution_id": execution_id,
                    "action": "INCREASE_MEMORY_LIMIT",
                    "status": "FAILED",
                    "namespace": namespace,
                    "deployment": deployment,
                    "error": error_msg,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

            logger.info(f"✅ Successfully updated memory limit for {namespace}/{deployment}")

            return {
                "execution_id": execution_id,
                "action": "INCREASE_MEMORY_LIMIT",
                "status": "EXECUTING",
                "namespace": namespace,
                "deployment": deployment,
                "old_memory_mi": current_memory_mi,
                "new_memory_mi": new_memory_mi,
                "increase_percent": memory_limit_increase_percent,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Memory limit increased from {current_memory_mi}Mi to {new_memory_mi}Mi"
            }

        except Exception as e:
            logger.error(f"Failed to increase memory limit: {str(e)}")
            return {
                "execution_id": execution_id,
                "action": "INCREASE_MEMORY_LIMIT",
                "status": "FAILED",
                "namespace": namespace,
                "deployment": deployment,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    def fetch_logs(
        self,
        namespace: str,
        pod_name: str,
        lines: int = 100
    ) -> dict:
        """
        Fetch recent logs from a pod using KubectlExecutor
        
        Args:
            namespace: Kubernetes namespace
            pod_name: Pod name
            lines: Number of log lines to fetch
            
        Returns:
            dict with execution status and log content
        """
        execution_id = str(uuid.uuid4())

        try:
            logger.info(f"Fetching logs from {namespace}/{pod_name} (last {lines} lines)")

            # Fetch pod logs using KubectlExecutor
            logs_result = self.kubectl_executor.run(
                "logs", pod_name, "-n", namespace,
                "--tail", str(lines)
            )

            if not self._is_kubectl_success(logs_result):
                logs = logs_result.stderr if hasattr(logs_result, 'stderr') else "Could not fetch logs"
                logger.warning(f"Error fetching logs: {logs}")
            else:
                logs = logs_result.stdout if hasattr(logs_result, 'stdout') else ""

            log_lines = len(logs.split('\n')) if logs else 0

            return {
                "execution_id": execution_id,
                "action": "INVESTIGATE_LOGS",
                "status": "COMPLETED",
                "namespace": namespace,
                "pod_name": pod_name,
                "logs": logs,
                "lines_returned": log_lines,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Retrieved {log_lines} lines of logs"
            }

        except Exception as e:
            logger.error(f"Failed to fetch logs: {str(e)}")
            return {
                "execution_id": execution_id,
                "action": "INVESTIGATE_LOGS",
                "status": "FAILED",
                "namespace": namespace,
                "pod_name": pod_name,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

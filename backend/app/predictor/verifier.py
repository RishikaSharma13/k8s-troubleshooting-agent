import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from loguru import logger

from app.models.risk import RiskIncident
from app.predictor.metrics_collector import MetricsCollector


class Verifier:
    """Verifies that remediation actually fixed the problem"""

    def __init__(self, metrics_collector: MetricsCollector):
        """
        Initialize verifier with metrics collector
        
        Args:
            metrics_collector: MetricsCollector instance to fetch current metrics
        """
        self.metrics_collector = metrics_collector
        self.improvement_threshold_percent = 30  # 30% improvement = success

    def verify_fix(
        self,
        incident: RiskIncident,
        execution_id: str,
        max_wait_seconds: int = 90
    ) -> dict:
        """
        After execution, monitor pod for improvement in risk score
        
        Waits up to max_wait_seconds, checking every 10 seconds.
        If risk score improves by >30%, remediation succeeded.
        If no improvement after all checks, remediation failed.
        
        Args:
            incident: RiskIncident that was remediated
            execution_id: ID of the remediation execution
            max_wait_seconds: Maximum time to wait for verification (default 90s)
            
        Returns:
            dict with verification status (RESOLVED, FAILED, TIMEOUT)
        """
        namespace = incident.trend_data.namespace
        pod_name = incident.trend_data.pod_name
        baseline_risk = incident.trend_data.risk_score

        logger.info(
            f"Starting verification for {namespace}/{pod_name} "
            f"(baseline risk: {baseline_risk}, execution_id: {execution_id})"
        )

        # Calculate how many attempts we can make
        check_interval = 10  # seconds between checks
        max_attempts = max_wait_seconds // check_interval

        current_risk = baseline_risk
        best_risk = baseline_risk

        # Poll metrics every 10 seconds
        for attempt in range(1, max_attempts + 1):
            logger.info(
                f"Verification attempt {attempt}/{max_attempts} for {namespace}/{pod_name}"
            )

            # Wait before checking
            time.sleep(check_interval)

            try:
                # Scan namespace to get current metrics
                current_metrics = self.metrics_collector.scan_namespace(namespace)

                # Find the pod in the results
                pod_risk = self._find_pod_risk(current_metrics, pod_name)

                if pod_risk is None:
                    logger.warning(
                        f"Could not find pod {pod_name} in metrics for verification"
                    )
                    continue

                current_risk = pod_risk

                # Calculate improvement
                risk_reduction = baseline_risk - current_risk
                improvement_percent = (
                    (risk_reduction / baseline_risk) * 100
                    if baseline_risk > 0
                    else 0
                )

                logger.info(
                    f"Verification attempt {attempt}: "
                    f"baseline={baseline_risk}, current={current_risk}, "
                    f"improvement={improvement_percent:.1f}%"
                )

                # Track best risk seen so far
                if current_risk < best_risk:
                    best_risk = current_risk

                # Success: risk dropped significantly
                if improvement_percent > self.improvement_threshold_percent:
                    logger.info(
                        f"✅ Remediation RESOLVED: Risk improved {improvement_percent:.1f}%"
                    )

                    return {
                        "execution_id": execution_id,
                        "verification_id": str(uuid.uuid4()),
                        "status": "RESOLVED",
                        "namespace": namespace,
                        "pod_name": pod_name,
                        "baseline_risk": baseline_risk,
                        "final_risk": current_risk,
                        "best_risk": best_risk,
                        "improvement_percent": round(improvement_percent, 2),
                        "attempts": attempt,
                        "max_attempts": max_attempts,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "message": f"Pod recovered successfully. Risk improved {improvement_percent:.1f}%"
                    }

            except Exception as e:
                logger.error(f"Error during verification attempt {attempt}: {str(e)}")
                continue

        # If we get here, no significant improvement was detected
        final_improvement = (
            ((baseline_risk - best_risk) / baseline_risk) * 100
            if baseline_risk > 0
            else 0
        )

        logger.warning(
            f"❌ Remediation FAILED: No significant improvement after {max_attempts} attempts. "
            f"Best improvement: {final_improvement:.1f}%"
        )

        return {
            "execution_id": execution_id,
            "verification_id": str(uuid.uuid4()),
            "status": "FAILED",
            "namespace": namespace,
            "pod_name": pod_name,
            "baseline_risk": baseline_risk,
            "final_risk": current_risk,
            "best_risk": best_risk,
            "improvement_percent": round(final_improvement, 2),
            "attempts": max_attempts,
            "max_attempts": max_attempts,
            "threshold_required": self.improvement_threshold_percent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": f"Pod did not improve after remediation. Best improvement: {final_improvement:.1f}%"
        }

    def _find_pod_risk(
        self,
        metrics: Dict[str, Any],
        pod_name: str
    ) -> Optional[float]:
        """
        Helper method to find a specific pod's risk score in metrics
        
        Args:
            metrics: Result from metrics_collector.scan_namespace()
            pod_name: Pod name to search for
            
        Returns:
            Risk score if found, None otherwise
        """
        if "risks" not in metrics:
            return None

        for risk_entry in metrics["risks"]:
            # Pod name might be truncated or have suffix, so we check if it starts with it
            if pod_name in risk_entry.get("pod_name", ""):
                return risk_entry.get("risk_score", None)

        return None

    def is_remediation_successful(
        self,
        baseline_risk: float,
        final_risk: float
    ) -> bool:
        """
        Determine if remediation was successful based on risk change
        
        Args:
            baseline_risk: Risk score before remediation
            final_risk: Risk score after remediation
            
        Returns:
            True if improvement > threshold, False otherwise
        """
        if baseline_risk == 0:
            return False

        improvement_percent = ((baseline_risk - final_risk) / baseline_risk) * 100
        return improvement_percent > self.improvement_threshold_percent

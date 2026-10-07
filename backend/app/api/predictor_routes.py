from fastapi import APIRouter, Query
from loguru import logger

from app.predictor.metrics_collector import MetricsCollector
from app.predictor.action_planner import ActionPlanner
from app.predictor.remediation_executor import RemediationExecutor
from app.predictor.verifier import Verifier
from app.kubernetes.kubectl_executor import KubectlExecutor


router = APIRouter(prefix="/api/v1", tags=["predictor"])


# Global predictor/remediation instances
collector = MetricsCollector()
kubectl_executor = KubectlExecutor()
remediation_executor = RemediationExecutor(kubectl_executor)
verifier = Verifier(collector)


@router.get("/risks/scan-namespace")
async def scan_namespace(namespace: str = Query("default")) -> dict:
    """
    Scan a namespace for pods with rising resource usage.
    """
    logger.info(f"Scanning namespace: {namespace}")

    result = collector.scan_namespace(namespace)

    return result


@router.get("/risks/pending-incidents")
async def get_pending_incidents() -> dict:
    """
    Get all pending risk incidents waiting for action.
    """
    logger.info("Fetching pending incidents")

    incidents = collector.get_pending_incidents()

    return {
        "status": "success",
        "count": len(incidents),
        "incidents": incidents,
    }


@router.post("/actions/recommend")
async def recommend_action(incident_id: str = Query(...)) -> dict:
    """
    Get action recommendation for a specific incident.
    """
    logger.info(f"Getting recommendation for incident: {incident_id}")

    # Get incident from risk manager
    incident = collector.risk_manager.get_incident(incident_id)

    if not incident:
        return {
            "status": "error",
            "error": f"Incident {incident_id} not found",
            "recommendation": None,
        }

    # Get trend data
    trend_data = incident.trend_data

    # Get memory values for trend calculation
    memory_values = [m.value for m in trend_data.metrics_history]

    slope, _ = collector.trend_detector.calculate_trend(
        memory_values
    )

    # Use the actual restart count from the incident
    actual_restart_count = trend_data.restart_count

    # Propose action
    action = ActionPlanner.propose_action(
        risk_score=trend_data.risk_score,
        risk_level=trend_data.risk_level,
        restart_count=actual_restart_count,
        memory_percent=memory_values[-1] if memory_values else 0,
        trend_slope=slope,
    )

    if not action:
        return {
            "status": "success",
            "incident_id": incident_id,
            "recommendation": None,
            "message": "No action recommended at this time",
        }

    return {
        "status": "success",
        "incident_id": incident_id,
        "pod_name": trend_data.pod_name,
        "deployment": trend_data.deployment_name,
        "namespace": trend_data.namespace,
        "risk_score": round(trend_data.risk_score, 2),
        "risk_level": trend_data.risk_level,
        "restart_count": actual_restart_count,
        "recommendation": action,
    }


@router.post("/actions/approve")
async def approve_action(
    incident_id: str = Query(...),
    action: str = Query(...),
) -> dict:
    """
    Approve a recommended action for an incident.
    """
    logger.info(
        f"Approving action {action} for incident {incident_id}"
    )

    # Validate action
    if not ActionPlanner.validate_action(action):
        return {
            "status": "error",
            "error": f"Invalid action: {action}",
            "incident_id": incident_id,
        }

    # Get incident first
    incident = collector.risk_manager.get_incident(incident_id)

    if not incident:
        return {
            "status": "error",
            "error": f"Incident {incident_id} not found",
            "incident_id": incident_id,
        }

    # Only pending incidents can be approved
    if incident.status != "PENDING":
        return {
            "status": "error",
            "error": (
                f"Incident must be PENDING before approval. "
                f"Current status: {incident.status}"
            ),
            "incident_id": incident_id,
        }

    # Update incident status
    success = collector.risk_manager.update_incident_status(
        incident_id,
        "APPROVED",
    )

    if not success:
        return {
            "status": "error",
            "error": f"Failed to approve incident {incident_id}",
            "incident_id": incident_id,
        }

    return {
        "status": "success",
        "incident_id": incident_id,
        "action": action,
        "new_status": "APPROVED",
        "message": "Action approved. Ready for execution.",
    }


@router.post("/actions/reject")
async def reject_action(
    incident_id: str = Query(...),
    reason: str = Query(""),
) -> dict:
    """
    Reject a recommended action for an incident.
    """
    logger.info(
        f"Rejecting action for incident {incident_id}. "
        f"Reason: {reason}"
    )

    # Get incident first
    incident = collector.risk_manager.get_incident(incident_id)

    if not incident:
        return {
            "status": "error",
            "error": f"Incident {incident_id} not found",
            "incident_id": incident_id,
        }

    # Only pending incidents can be rejected
    if incident.status != "PENDING":
        return {
            "status": "error",
            "error": (
                f"Incident must be PENDING before rejection. "
                f"Current status: {incident.status}"
            ),
            "incident_id": incident_id,
        }

    # Update incident status
    success = collector.risk_manager.update_incident_status(
        incident_id,
        "REJECTED",
    )

    if not success:
        return {
            "status": "error",
            "error": f"Failed to reject incident {incident_id}",
            "incident_id": incident_id,
        }

    return {
        "status": "success",
        "incident_id": incident_id,
        "new_status": "REJECTED",
        "reason": reason or "No reason provided",
        "message": "Action rejected.",
    }


@router.post("/remediation/execute")
async def execute_remediation(
    incident_id: str = Query(...),
    action: str = Query(...),
) -> dict:
    """
    Execute an approved remediation action.
    """
    logger.info(
        f"Executing remediation {action} "
        f"for incident {incident_id}"
    )

    # Get incident
    incident = collector.risk_manager.get_incident(incident_id)

    if not incident:
        return {
            "status": "error",
            "error": f"Incident {incident_id} not found",
            "incident_id": incident_id,
        }

    # Safety gate: execution requires explicit approval
    if incident.status != "APPROVED":
        return {
            "status": "error",
            "error": (
                f"Incident must be APPROVED before execution. "
                f"Current status: {incident.status}"
            ),
            "incident_id": incident_id,
        }

    # Validate action
    if not ActionPlanner.validate_action(action):
        return {
            "status": "error",
            "error": f"Invalid action: {action}",
            "incident_id": incident_id,
        }

    # Execute remediation
    execution_result = remediation_executor.execute_action(
        incident=incident,
        action_type=action,
    )

    execution_status = execution_result.get("status")

    if execution_status in {"EXECUTING", "COMPLETED"}:
        new_status = "EXECUTING"
        response_status = "success"
    else:
        new_status = "FAILED"
        response_status = "error"

    # Update incident lifecycle
    collector.risk_manager.update_incident_status(
        incident_id,
        new_status,
    )

    return {
        "status": response_status,
        "incident_id": incident_id,
        "action": action,
        "new_status": new_status,
        "execution_id": execution_result.get("execution_id"),
        "execution_details": execution_result,
    }


@router.post("/remediation/verify")
async def verify_remediation(
    incident_id: str = Query(...),
    execution_id: str = Query(...),
) -> dict:
    """
    Verify whether the remediation actually fixed the issue.
    """
    logger.info(
        f"Verifying remediation for incident {incident_id}, "
        f"execution {execution_id}"
    )

    # Get incident
    incident = collector.risk_manager.get_incident(incident_id)

    if not incident:
        return {
            "status": "error",
            "error": f"Incident {incident_id} not found",
            "incident_id": incident_id,
        }

    # Verification only makes sense after execution started
    if incident.status != "EXECUTING":
        return {
            "status": "error",
            "error": (
                f"Incident must be EXECUTING before verification. "
                f"Current status: {incident.status}"
            ),
            "incident_id": incident_id,
        }

    # Run verification
    verification_result = verifier.verify_fix(
        incident=incident,
        execution_id=execution_id,
    )

    verification_status = verification_result.get("status")

    if verification_status == "RESOLVED":
        new_status = "RESOLVED"
    else:
        new_status = "FAILED"

    # Update incident lifecycle
    collector.risk_manager.update_incident_status(
        incident_id,
        new_status,
    )

    return {
        "status": "success",
        "incident_id": incident_id,
        "verification_result": verification_result,
        "new_incident_status": new_status,
    }


@router.get("/remediation/status")
async def remediation_status(
    execution_id: str = Query(...),
) -> dict:
    """
    Return execution status.

    Detailed persistent execution tracking will be added
    in a later phase.
    """
    return {
        "status": "success",
        "execution_id": execution_id,
        "message": (
            "Execution status tracking is currently in-memory. "
            "Persistent execution history will be added in Phase 2."
        ),
    }


@router.get("/health")
async def health_check() -> dict:
    """
    Health check for predictor module.
    """
    return {
        "status": "healthy",
        "module": "predictor",
        "features": [
            "anomaly-detection",
            "trend-analysis",
            "action-planning",
            "remediation",
            "verification",
        ],
    }

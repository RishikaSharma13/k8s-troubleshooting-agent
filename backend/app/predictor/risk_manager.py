
from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

from loguru import logger

from app.models.risk import TrendData, RiskIncident, Metric
from app.predictor.trend_detector import TrendDetector


class RiskManager:
    """Manages pod risk tracking and incident creation."""

    def __init__(self):
        # In-memory storage for the initial implementation.
        # A persistent store can be added in a later phase.
        self.trends: Dict[str, TrendData] = {}
        self.incidents: Dict[str, RiskIncident] = {}
        self.max_history = 10

    def update_pod_metrics(
        self,
        namespace: str,
        pod_name: str,
        deployment_name: str,
        memory_percent: float,
        restart_count: int = 0,
    ) -> Optional[TrendData]:
        """Store a metric reading and recalculate pod risk."""

        if not 0 <= memory_percent <= 100:
            raise ValueError("memory_percent must be between 0 and 100")

        if restart_count < 0:
            raise ValueError("restart_count cannot be negative")

        key = f"{namespace}/{pod_name}"
        now = datetime.now(timezone.utc)

        if key not in self.trends:
            self.trends[key] = TrendData(
                namespace=namespace,
                pod_name=pod_name,
                deployment_name=deployment_name,
                metrics_history=[],
                risk_score=0.0,
                risk_level="NORMAL",
                last_updated=now,
                created_at=now,
            )

        trend = self.trends[key]

        # Store the new reading.
        trend.metrics_history.append(
            Metric(timestamp=now, value=memory_percent)
        )

        # Keep only the latest readings.
        trend.metrics_history = trend.metrics_history[-self.max_history:]

        # Calculate trend from the recent metric values.
        values = [metric.value for metric in trend.metrics_history]
        slope, direction = TrendDetector.calculate_trend(values)

        # Calculate the risk score and risk level.
        trend.risk_score = TrendDetector.calculate_risk_score(
            current_value=memory_percent,
            trend_slope=slope,
            restart_count=restart_count,
        )
        trend.risk_level = TrendDetector.get_risk_level(trend.risk_score)
        trend.last_updated = now

        logger.info(
            "Updated risk for {}/{}: value={}%, "
            "trend={}, slope={}, score={}, level={}",
            namespace,
            pod_name,
            memory_percent,
            direction,
            slope,
            trend.risk_score,
            trend.risk_level,
        )

        return trend

    def create_incident(
        self,
        trend_data: TrendData,
        recommended_action: str,
        confidence: float,
    ) -> RiskIncident:
        """Create a risk incident."""

        if not 0 <= confidence <= 100:
            raise ValueError("confidence must be between 0 and 100")

        incident = RiskIncident(
            id=str(uuid.uuid4()),
            trend_data=trend_data,
            recommended_action=recommended_action,
            confidence=confidence,
            status="PENDING",
            created_at=datetime.now(timezone.utc),
        )

        self.incidents[incident.id] = incident
        logger.info(
            "Created incident {}: {}",
            incident.id,
            recommended_action,
        )
        return incident

    def get_incident(self, incident_id: str) -> Optional[RiskIncident]:
        """Get an incident by ID."""
        return self.incidents.get(incident_id)

    def get_pending_incidents(self) -> List[RiskIncident]:
        """Get all pending incidents."""
        return [
            incident
            for incident in self.incidents.values()
            if incident.status == "PENDING"
        ]

    def update_incident_status(
        self,
        incident_id: str,
        status: str,
    ) -> bool:
        """Update an incident's status."""

        allowed_statuses = {
            "PENDING",
            "APPROVED",
            "EXECUTING",
            "RESOLVED",
            "FAILED",
            "REJECTED",
        }

        if status not in allowed_statuses:
            raise ValueError(
                f"Invalid status '{status}'. "
                f"Allowed statuses: {sorted(allowed_statuses)}"
            )

        incident = self.incidents.get(incident_id)
        if incident is None:
            return False

        incident.status = status
        logger.info("Incident {} status: {}", incident_id, status)
        return True

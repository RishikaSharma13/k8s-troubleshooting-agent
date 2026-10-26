from sqlalchemy.orm import Session
from app.models.database import SessionLocal, Incident, Execution, MetricsHistory
from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

from loguru import logger

from app.models.risk import TrendData, RiskIncident, Metric
from app.predictor.trend_detector import TrendDetector


class RiskManager:
    """Manages pod risk tracking and persistent incident storage."""

    def __init__(self):
        # Trend data remains in memory because it is used for
        # short-term trend calculation.
        self.trends: Dict[str, TrendData] = {}
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
                restart_count=0,
                last_updated=now,
            )

        trend = self.trends[key]

        trend.metrics_history.append(
            Metric(timestamp=now, value=memory_percent)
        )

        trend.metrics_history = trend.metrics_history[-self.max_history:]

        values = [metric.value for metric in trend.metrics_history]
        slope, direction = TrendDetector.calculate_trend(values)

        trend.risk_score = TrendDetector.calculate_risk_score(
            current_value=memory_percent,
            trend_slope=slope,
            restart_count=restart_count,
        )
        trend.risk_level = TrendDetector.get_risk_level(trend.risk_score)
        trend.restart_count = restart_count
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

        # Save metrics to database for forecasting
        try:
            db = SessionLocal()
            metric_record = MetricsHistory(
                pod_name=pod_name,
                namespace=namespace,
                deployment=deployment_name,
                memory_percent=memory_percent,
                cpu_percent=0.0,
                restart_count=restart_count,
                risk_score=trend.risk_score,
                recorded_at=now,
            )
            db.add(metric_record)
            db.commit()
            db.close()
            logger.info(f"Saved metrics for {namespace}/{pod_name} to database")
        except Exception as e:
            logger.error(f"Failed to save metrics to database: {e}")
        return trend

    @staticmethod
    def _to_risk_incident(db_incident: Incident) -> RiskIncident:
        """Convert a database Incident into the application's RiskIncident."""

        created_at = db_incident.created_at

        # PostgreSQL DateTime may return a naive datetime.
        # Normalize it to UTC for the application model.
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        trend_data = TrendData(
            namespace=db_incident.namespace,
            pod_name=db_incident.pod_name,
            deployment_name=db_incident.deployment,
            metrics_history=[],
            risk_score=db_incident.risk_score,
            risk_level=db_incident.risk_level,
            restart_count=0,
            last_updated=created_at or datetime.now(timezone.utc),
        )

        return RiskIncident(
            id=db_incident.id,
            trend_data=trend_data,
            recommended_action=db_incident.recommended_action or "",
            confidence=db_incident.confidence or 0.0,
            status=db_incident.status,
            created_at=created_at,
            approved_action=db_incident.approved_action,
        )

    def create_incident(
        self,
        trend_data: TrendData,
        recommended_action: str,
        confidence: float,
        reasoning: str = "",  # 🆕 Added
    ) -> RiskIncident:
        """Create and persist a risk incident."""

        if not 0 <= confidence <= 100:
            raise ValueError("confidence must be between 0 and 100")

        incident_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc)

        db_incident = Incident(
            id=incident_id,
            pod_name=trend_data.pod_name,
            deployment=trend_data.deployment_name,
            namespace=trend_data.namespace,
            risk_score=trend_data.risk_score,
            risk_level=trend_data.risk_level,
            status="PENDING",
            recommended_action=recommended_action,
            confidence=confidence,
            reasoning=reasoning,  # 🆕 Added
        )

        db = SessionLocal()

        try:
            db.add(db_incident)
            db.commit()
            db.refresh(db_incident)

            logger.info(
                "Created incident {}: {}",
                db_incident.id,
                recommended_action,
            )

            return self._to_risk_incident(db_incident)

        except Exception:
            db.rollback()
            logger.exception("Failed to create incident")
            raise

        finally:
            db.close()

    def get_incident(
        self,
        incident_id: str,
    ) -> Optional[RiskIncident]:
        """Get an incident from PostgreSQL."""

        db = SessionLocal()

        try:
            incident = (
                db.query(Incident)
                .filter(Incident.id == incident_id)
                .first()
            )

            if incident is None:
                return None

            return self._to_risk_incident(incident)

        finally:
            db.close()

    def get_pending_incidents(self) -> List[RiskIncident]:
        """Get all pending incidents from PostgreSQL."""

        db = SessionLocal()

        try:
            incidents = (
                db.query(Incident)
                .filter(Incident.status == "PENDING")
                .order_by(Incident.created_at.asc())
                .all()
            )

            return [
                self._to_risk_incident(incident)
                for incident in incidents
            ]

        finally:
            db.close()

    def get_pending_incident_for_pod(
        self,
        namespace: str,
        pod_name: str,
    ) -> Optional[RiskIncident]:
        """Return the latest pending incident for a pod."""

        db = SessionLocal()

        try:
            incident = (
                db.query(Incident)
                .filter(
                    Incident.status == "PENDING",
                    Incident.namespace == namespace,
                    Incident.pod_name == pod_name,
                )
                .order_by(Incident.created_at.desc())
                .first()
            )

            if incident is None:
                return None

            return self._to_risk_incident(incident)

        finally:
            db.close()

    def approve_incident(self, incident_id: str, action: str) -> bool:
        """Approve a PENDING incident for one exact action, in a single update.

        Returns False if the incident does not exist or is not PENDING. Execution
        later refuses any action other than the one recorded here.
        """
        db = SessionLocal()
        try:
            incident = (
                db.query(Incident)
                .filter(Incident.id == incident_id, Incident.status == "PENDING")
                .first()
            )
            if incident is None:
                return False
            incident.status = "APPROVED"
            incident.approved_action = action
            incident.approved_at = datetime.utcnow()
            db.commit()
            logger.info(f"Incident {incident_id} status: APPROVED for action {action}")
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def update_incident_status(
        self,
        incident_id: str,
        status: str,
    ) -> bool:
        """Update an incident's status in PostgreSQL."""

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

        db = SessionLocal()

        try:
            incident = (
                db.query(Incident)
                .filter(Incident.id == incident_id)
                .first()
            )

            if incident is None:
                return False

            incident.status = status

            if status == "APPROVED":
                incident.approved_at = datetime.now(timezone.utc)

            if status == "RESOLVED":
                incident.resolved_at = datetime.now(timezone.utc)

            db.commit()

            logger.info(
                "Incident {} status: {}",
                incident_id,
                status,
            )

            return True

        except Exception:
            db.rollback()
            logger.exception(
                "Failed to update incident {}",
                incident_id,
            )
            raise

        finally:
            db.close()

    def get_all_pods_in_namespace(self, namespace):
        """Get all pods in a namespace"""
        try:
            db = SessionLocal()
            # Get distinct pod names (using order_by to get most recent)
            pods = db.query(MetricsHistory).filter(
                MetricsHistory.namespace == namespace
            ).order_by(MetricsHistory.pod_name, MetricsHistory.recorded_at.desc()).distinct(MetricsHistory.pod_name).all()
            db.close()
            
            return list(set([
                p.pod_name
                for p in pods
            ]))
        except Exception as e:
            logger.error(f"get_all_pods failed: {e}")
            return []

    def get_pod_metrics_history(self, pod_name, namespace, limit=100):
        """Get historical metrics for a pod"""
        try:
            db = SessionLocal()
            metrics = db.query(MetricsHistory).filter(
                MetricsHistory.pod_name == pod_name,
                MetricsHistory.namespace == namespace
            ).order_by(MetricsHistory.recorded_at.desc()).limit(limit).all()
            db.close()
            
            # Return in chronological order (oldest first)
            return list(reversed(metrics))
        except Exception as e:
            logger.error(f"get_pod_metrics_history failed: {e}")
            return []

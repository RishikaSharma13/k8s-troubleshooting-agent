from fastapi import APIRouter, Query
from datetime import timedelta, datetime
from app.models.database import MetricsHistory, SessionLocal
from loguru import logger

router = APIRouter(prefix="/api/v1", tags=["metrics"])

@router.get("/metrics/history")
async def get_metrics_history(
    pod_name: str = Query(...),
    namespace: str = Query("default"),
    hours: int = Query(1),
) -> dict:
    """
    Get historical metrics for a pod (for charts/graphing).
    """
    try:
        db = SessionLocal()
        
        # Get metrics from last N hours
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        metrics = db.query(MetricsHistory).filter(
            MetricsHistory.pod_name == pod_name,
            MetricsHistory.namespace == namespace,
            MetricsHistory.recorded_at >= cutoff_time
        ).order_by(MetricsHistory.recorded_at).all()
        
        db.close()
        
        return {
            "status": "success",
            "pod_name": pod_name,
            "namespace": namespace,
            "count": len(metrics),
            "metrics": [
                {
                    "memory_percent": m.memory_percent,
                    "cpu_percent": m.cpu_percent,
                    "restart_count": m.restart_count,
                    "recorded_at": m.recorded_at.isoformat() if m.recorded_at else None
                }
                for m in metrics
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching metrics history: {e}")
        return {"status": "error", "message": str(e)}

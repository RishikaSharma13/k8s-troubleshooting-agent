from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List

@dataclass
class Metric:
    """Single metric reading"""
    timestamp: datetime
    value: float  # percentage (0-100)
    
@dataclass
class TrendData:
    """Trend data for a pod's resource usage"""
    namespace: str
    pod_name: str
    deployment_name: str
    metrics_history: List[Metric]
    risk_score: float
    risk_level: str
    # ← All fields with defaults must come after fields without defaults
    restart_count: int = 0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
@dataclass
class RiskIncident:
    """A risk that requires action"""
    id: str
    trend_data: TrendData
    
    recommended_action: str  # e.g., "SCALE_DEPLOYMENT"
    confidence: float  # 0-100
    
    status: str  # "PENDING", "APPROVED", "EXECUTING", "RESOLVED", "FAILED"
    created_at: datetime

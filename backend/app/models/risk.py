from datetime import datetime
from dataclasses import dataclass
from typing import List

@dataclass
class Metric:
    """Single metric reading"""
    timestamp: datetime
    value: float  # percentage (0-100)
    
@dataclass
class TrendData:
    """Trend information for one pod"""
    namespace: str
    pod_name: str
    deployment_name: str
    
    metrics_history: List[Metric]
    risk_score: float  # 0-100
    risk_level: str  # "NORMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"
    
    last_updated: datetime
    created_at: datetime

@dataclass
class RiskIncident:
    """A risk that requires action"""
    id: str
    trend_data: TrendData
    
    recommended_action: str  # e.g., "SCALE_DEPLOYMENT"
    confidence: float  # 0-100
    
    status: str  # "PENDING", "APPROVED", "EXECUTING", "RESOLVED", "FAILED"
    created_at: datetime

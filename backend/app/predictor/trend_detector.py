from typing import List, Tuple, Optional
from loguru import logger

class TrendDetector:
    """Detects rising trends in metrics"""
    
    @staticmethod
    def calculate_trend(values: List[float]) -> Tuple[float, str]:
        """
        Analyze trend in recent values
        
        Returns: (slope, direction)
        """
        if len(values) < 2:
            return 0.0, "stable"
        
        diffs = [values[i+1] - values[i] for i in range(len(values)-1)]
        avg_slope = sum(diffs) / len(diffs) if diffs else 0.0
        
        if avg_slope > 2.0:
            direction = "rising"
        elif avg_slope < -2.0:
            direction = "falling"
        else:
            direction = "stable"
        
        return avg_slope, direction
    
    @staticmethod
    def calculate_risk_score(
        current_value: float,
        trend_slope: float,
        restart_count: int
    ) -> float:
        """Calculate overall risk score (0-100)"""
        
        risk = 0.0
        
        # Component 1: Current memory usage (0-30 points)
        if current_value > 70:
            risk += (current_value - 70) / 30 * 30
        
        # Component 2: Trend component (0-40 points)
        trend_risk = min(trend_slope / 5.0 * 40, 40)
        risk += max(trend_risk, 0)
        
        # Component 3: Restart pattern (0-30 points)
        if restart_count > 3:
            risk += min(restart_count * 5, 30)
        
        return min(risk, 100.0)
    
    @staticmethod
    def get_risk_level(risk_score: float) -> str:
        """Convert risk score to level"""
        if risk_score >= 75:
            return "CRITICAL"
        elif risk_score >= 60:
            return "HIGH"
        elif risk_score >= 40:
            return "MEDIUM"
        elif risk_score >= 20:
            return "LOW"
        else:
            return "NORMAL"

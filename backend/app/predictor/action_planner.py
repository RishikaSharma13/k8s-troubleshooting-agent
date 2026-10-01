from enum import Enum
from loguru import logger
from typing import Optional

class ActionType(Enum):
    """Types of remediation actions"""
    SCALE_DEPLOYMENT = "SCALE_DEPLOYMENT"
    RESTART_DEPLOYMENT = "RESTART_DEPLOYMENT"
    INCREASE_MEMORY_LIMIT = "INCREASE_MEMORY_LIMIT"
    INVESTIGATE_LOGS = "INVESTIGATE_LOGS"


class ActionPlanner:
    """Plans remediation actions based on risk analysis"""
    
    @staticmethod
    def propose_action(
        risk_score: float,
        risk_level: str,
        restart_count: int,
        memory_percent: float,
        trend_slope: float
    ) -> Optional[dict]:
        """
        Propose an action based on risk metrics
        
        Returns: {action, parameters, confidence, reasoning}
        """
        
        # No action needed
        if risk_score < 20:
            return None
        
        # Rule 1: High memory + rising trend + few restarts → SCALE
        if memory_percent > 70 and trend_slope > 2.0 and restart_count <= 2:
            return {
                "action": ActionType.SCALE_DEPLOYMENT.value,
                "parameters": {
                    "scale_increase_percent": 50,  # Increase replicas by 50%
                },
                "confidence": 80.0,
                "reasoning": "Memory rising rapidly with few restarts. More replicas will distribute load.",
            }
        
        # Rule 2: High memory + many restarts → INCREASE LIMIT
        if memory_percent > 80 and restart_count > 3:
            return {
                "action": ActionType.INCREASE_MEMORY_LIMIT.value,
                "parameters": {
                    "memory_limit_increase_percent": 30,  # Increase limit by 30%
                },
                "confidence": 75.0,
                "reasoning": "High memory with frequent restarts. Likely OOMKilled. Increase memory limit.",
            }
        
        # Rule 3: Very high restarts → INVESTIGATE
        if restart_count > 5:
            return {
                "action": ActionType.INVESTIGATE_LOGS.value,
                "parameters": {
                    "lines": 100,
                },
                "confidence": 90.0,
                "reasoning": "Excessive restarts. Check logs to find root cause.",
            }
        
        # Rule 4: Generic high risk → SCALE
        if risk_score >= 60:
            return {
                "action": ActionType.SCALE_DEPLOYMENT.value,
                "parameters": {
                    "scale_increase_percent": 30,
                },
                "confidence": 65.0,
                "reasoning": "High risk detected. Scale deployment as precaution.",
            }
        
        # Rule 5: Medium risk → INVESTIGATE
        if risk_score >= 40:
            return {
                "action": ActionType.INVESTIGATE_LOGS.value,
                "parameters": {
                    "lines": 50,
                },
                "confidence": 70.0,
                "reasoning": "Medium risk detected. Investigate logs for issues.",
            }
        
        # Low risk but something detected
        return {
            "action": ActionType.INVESTIGATE_LOGS.value,
            "parameters": {
                "lines": 30,
            },
            "confidence": 50.0,
            "reasoning": "Low risk detected. Monitor logs.",
        }
    
    @staticmethod
    def validate_action(action: str) -> bool:
        """Validate that action is a known type"""
        try:
            ActionType[action]
            return True
        except KeyError:
            return False

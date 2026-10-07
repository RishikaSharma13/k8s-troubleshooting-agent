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
        
        Decision tree:
        1. VERY HIGH memory (>85%) alone → INCREASE_MEMORY_LIMIT
        2. HIGH memory (>70%) + rising trend + few restarts → SCALE_DEPLOYMENT
        3. HIGH memory (>80%) + many restarts (>3) → INCREASE_MEMORY_LIMIT
        4. Excessive restarts (>5) → INVESTIGATE_LOGS
        5. High risk score → SCALE_DEPLOYMENT
        6. Medium risk → INVESTIGATE_LOGS
        7. Low risk → INVESTIGATE_LOGS
        
        Returns: {action, parameters, confidence, reasoning}
        """
        
        # No action needed for very low risk
        if risk_score < 20:
            return None
        
        # PRIORITY 1: Very high memory utilization alone is a strong indicator
        # Pod is using 85%+ of limit - likely to OOMKill soon
        if memory_percent > 85:
            return {
                "action": ActionType.INCREASE_MEMORY_LIMIT.value,
                "parameters": {
                    "memory_limit_increase_percent": 50,  # Increase by 50% for safety
                },
                "confidence": 95.0,
                "reasoning": f"Critical: Memory utilization at {memory_percent:.1f}% of limit. Pod is at risk of OOMKill. Increase memory limit immediately.",
            }
        
        # PRIORITY 2: High memory + rising trend + few restarts → Distribute load
        # Not a memory capacity issue, but a load spike
        if memory_percent > 70 and trend_slope > 2.0 and restart_count <= 2:
            return {
                "action": ActionType.SCALE_DEPLOYMENT.value,
                "parameters": {
                    "scale_increase_percent": 50,
                },
                "confidence": 80.0,
                "reasoning": f"Memory rising rapidly ({trend_slope:.1f}% trend slope) with few restarts. More replicas will distribute load.",
            }
        
        # PRIORITY 3: High memory + many restarts → Increase capacity
        # Likely OOMKilled repeatedly, needs more memory
        if memory_percent > 75 and restart_count > 3:
            return {
                "action": ActionType.INCREASE_MEMORY_LIMIT.value,
                "parameters": {
                    "memory_limit_increase_percent": 40,
                },
                "confidence": 85.0,
                "reasoning": f"Memory at {memory_percent:.1f}% with {restart_count} restarts indicates repeated OOMKills. Increase memory limit.",
            }
        
        # PRIORITY 4: Excessive restarts with unknown cause → Investigate
        # Need to understand WHY it's restarting
        if restart_count > 5:
            return {
                "action": ActionType.INVESTIGATE_LOGS.value,
                "parameters": {
                    "lines": 100,
                },
                "confidence": 90.0,
                "reasoning": f"Excessive restarts ({restart_count}). Check logs to identify root cause before attempting fix.",
            }
        
        # PRIORITY 5: High memory + no specific pattern → Try scaling first
        # Safest bet when memory is high but pattern is unclear
        if memory_percent > 70:
            return {
                "action": ActionType.SCALE_DEPLOYMENT.value,
                "parameters": {
                    "scale_increase_percent": 30,
                },
                "confidence": 70.0,
                "reasoning": f"Memory at {memory_percent:.1f}%. Scale deployment to distribute load.",
            }
        
        # PRIORITY 6: Generic high risk → Scale as precaution
        if risk_score >= 60:
            return {
                "action": ActionType.SCALE_DEPLOYMENT.value,
                "parameters": {
                    "scale_increase_percent": 30,
                },
                "confidence": 65.0,
                "reasoning": "High risk detected. Scale deployment as precaution.",
            }
        
        # PRIORITY 7: Medium risk → Investigate
        if risk_score >= 40:
            return {
                "action": ActionType.INVESTIGATE_LOGS.value,
                "parameters": {
                    "lines": 50,
                },
                "confidence": 70.0,
                "reasoning": "Medium risk detected. Investigate logs for issues.",
            }
        
        # PRIORITY 8: Low risk but detected → Monitor
        return {
            "action": ActionType.INVESTIGATE_LOGS.value,
            "parameters": {
                "lines": 30,
            },
            "confidence": 50.0,
            "reasoning": "Low risk detected. Monitor logs for warnings.",
        }
    
    @staticmethod
    def validate_action(action: str) -> bool:
        """Validate that action is a known type"""
        try:
            ActionType[action]
            return True
        except KeyError:
            return False

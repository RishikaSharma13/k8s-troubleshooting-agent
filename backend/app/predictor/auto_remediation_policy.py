from enum import Enum
from loguru import logger


class RiskLevel(Enum):
    """Risk classification based on escalation probability"""
    LOW = "LOW"          # < 40% — Auto-execute ✅
    MEDIUM = "MEDIUM"    # 40-70% — Manual approval needed
    HIGH = "HIGH"        # > 70% — Always manual


class AutoRemediationPolicy:
    """Policy for deciding when to auto-remediate vs manual approval"""
    
    # Thresholds
    LOW_THRESHOLD = 0.40      # 40%
    MEDIUM_THRESHOLD = 0.70   # 70%
    
    @staticmethod
    def classify_risk(escalation_probability: float) -> RiskLevel:
        """
        Classify risk level based on escalation probability.
        
        Args:
            escalation_probability: 0.0 to 1.0 (0% to 100%)
        
        Returns:
            RiskLevel enum (LOW, MEDIUM, HIGH)
        """
        if escalation_probability < AutoRemediationPolicy.LOW_THRESHOLD:
            return RiskLevel.LOW
        elif escalation_probability < AutoRemediationPolicy.MEDIUM_THRESHOLD:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.HIGH
    
    @staticmethod
    def should_auto_remediate(escalation_probability: float) -> bool:
        """
        Decide if auto-remediation should be triggered.
        
        Returns:
            True if risk is LOW (< 40%) → execute automatically
            False if risk is MEDIUM/HIGH → require manual approval
        """
        return escalation_probability < AutoRemediationPolicy.LOW_THRESHOLD
    
    @staticmethod
    def get_action_recommendation(risk_level: RiskLevel) -> str:
        """Get recommended action based on risk level"""
        if risk_level == RiskLevel.LOW:
            return "Auto-execute remediation (restart pod)"
        elif risk_level == RiskLevel.MEDIUM:
            return "Manual approval required before remediation"
        else:
            return "URGENT: High risk detected - manual review required"
    
    @staticmethod
    def get_remediation_action(pod_name: str, risk_level: RiskLevel) -> dict:
        """
        Determine what remediation action to take.
        
        Args:
            pod_name: Name of the pod
            risk_level: Classified risk level
        
        Returns:
            Dict with action details: { action, parameters, reason }
        """
        if risk_level == RiskLevel.LOW:
            return {
                "pod_name": pod_name,
                "action": "restart",
                "auto_execute": True,
                "reason": "Low risk - safe to auto-execute"
            }
        elif risk_level == RiskLevel.MEDIUM:
            return {
                "pod_name": pod_name,
                "action": "scale_up",
                "auto_execute": False,
                "reason": "Medium risk - manual approval needed before scaling"
            }
        else:
            return {
                "pod_name": pod_name,
                "action": "alert_only",
                "auto_execute": False,
                "reason": "High risk - manual intervention required"
            }


if __name__ == "__main__":
    # Quick test
    policy = AutoRemediationPolicy()
    
    test_risks = [0.2, 0.5, 0.8]
    for risk in test_risks:
        level = policy.classify_risk(risk)
        auto = policy.should_auto_remediate(risk)
        recommendation = policy.get_action_recommendation(level)
        print(f"Risk: {risk*100:.0f}% → Level: {level.value}, Auto: {auto}, Action: {recommendation}")

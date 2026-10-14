

# ========== FORECASTING ENDPOINTS ==========

@router.get("/forecast/train")
async def train_forecast(namespace: str = Query("default")) -> dict:
    """Train Prophet model on historical metrics"""
    logger.info(f"Training forecast for namespace: {namespace}")
    
    try:
        from app.predictor.forecaster import Forecaster
        forecaster = Forecaster()
        
        # Get all pods in namespace
        pods = collector.risk_manager.get_all_pods_in_namespace(namespace)
        
        trained_count = 0
        for pod in pods:
            # Get metrics history for this pod
            metrics = collector.risk_manager.get_pod_metrics_history(pod['pod_name'], pod['namespace'])
            if metrics and len(metrics) >= 3:
                success = forecaster.train(pod['pod_name'], metrics)
                if success:
                    trained_count += 1
        
        return {
            "status": "success",
            "namespace": namespace,
            "models_trained": trained_count,
            "message": f"Trained {trained_count} forecasting models"
        }
    except Exception as e:
        logger.error(f"Training failed: {e}")
        return {"status": "error", "error": str(e)}


@router.get("/forecast/predict")
async def predict_escalation(pod_name: str = Query(...), namespace: str = Query("default"), hours_ahead: int = Query(1)) -> dict:
    """Predict future resource usage for a pod"""
    logger.info(f"Predicting for {pod_name} in {namespace}, {hours_ahead} hours ahead")
    
    try:
        from app.predictor.forecaster import Forecaster
        forecaster = Forecaster()
        
        # Get metrics history
        metrics = collector.risk_manager.get_pod_metrics_history(pod_name, namespace)
        
        # Train model
        if metrics and len(metrics) >= 3:
            forecaster.train(pod_name, metrics)
            prediction = forecaster.predict(pod_name, hours_ahead)
            
            if prediction:
                return {
                    "status": "success",
                    "prediction": prediction
                }
        
        return {
            "status": "error",
            "error": "Insufficient data for prediction"
        }
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        return {"status": "error", "error": str(e)}


@router.get("/forecast/escalation-risk")
async def get_escalation_risk(namespace: str = Query("default"), critical_threshold: float = Query(80.0)) -> dict:
    """Get escalation risk for all pods in namespace"""
    logger.info(f"Getting escalation risks for {namespace}")
    
    try:
        from app.predictor.forecaster import Forecaster
        forecaster = Forecaster()
        
        pods = collector.risk_manager.get_all_pods_in_namespace(namespace)
        risks = []
        
        for pod in pods:
            metrics = collector.risk_manager.get_pod_metrics_history(pod['pod_name'], pod['namespace'])
            if metrics and len(metrics) >= 3:
                forecaster.train(pod['pod_name'], metrics)
                risk = forecaster.escalation_risk(pod['pod_name'], metrics, critical_threshold)
                if risk:
                    risks.append(risk)
        
        # Sort by escalation probability (highest first)
        risks = sorted(risks, key=lambda x: x['escalation_probability'], reverse=True)
        
        return {
            "status": "success",
            "namespace": namespace,
            "critical_threshold": critical_threshold,
            "risk_count": len(risks),
            "risks": risks
        }
    except Exception as e:
        logger.error(f"Escalation risk failed: {e}")
        return {"status": "error", "error": str(e)}

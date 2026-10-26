import React, { useState, useEffect } from 'react';
import '../styles/ForecastingChart.css';

const ForecastingChart = ({ incidentId, podName, namespace = 'default' }) => {
  const [forecast, setForecast] = useState(null);
  const [escalationRisk, setEscalationRisk] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchForecast = async () => {
      try {
        setLoading(true);

        // Fetch prediction
        const predRes = await fetch(
          `http://127.0.0.1:8000/api/v1/forecast/predict?pod_name=${podName}&namespace=${namespace}&hours_ahead=1`
        );
        const predData = await predRes.json();

        // Fetch escalation risk
        const riskRes = await fetch(
          `http://127.0.0.1:8000/api/v1/forecast/escalation-risk?namespace=${namespace}`
        );
        const riskData = await riskRes.json();

        if (predData.status === 'success' && predData.prediction) {
          setForecast(predData.prediction);
        }

        if (riskData.status === 'success' && riskData.escalation_risks[podName]) {
          setEscalationRisk(riskData.escalation_risks[podName]);
        }

        setLoading(false);
      } catch (err) {
        setError(err.message);
        setLoading(false);
      }
    };

    if (podName) {
      fetchForecast();
      const interval = setInterval(fetchForecast, 10000); // Refresh every 10s
      return () => clearInterval(interval);
    }
  }, [podName, namespace]);

  const getRiskColor = (probability) => {
    if (probability < 0.4) return 'green';   // LOW
    if (probability < 0.7) return 'yellow';  // MEDIUM
    return 'red';                            // HIGH
  };

  const getRiskLevel = (probability) => {
    if (probability < 0.4) return 'LOW ✅';
    if (probability < 0.7) return 'MEDIUM ⚠️';
    return 'HIGH 🔴';
  };

  if (loading) return <div className="forecast-loading">📊 Loading forecast...</div>;
  if (error) return <div className="forecast-error">❌ {error}</div>;
  if (!forecast && !escalationRisk) return <div className="forecast-none">No forecast data</div>;

  const riskProb = escalationRisk?.escalation_probability || 0;
  const riskColor = getRiskColor(riskProb);
  const riskLevel = getRiskLevel(riskProb);

  return (
    <div className="forecast-card">
      <h3>📈 Forecast & Risk Analysis</h3>

      {forecast && (
        <div className="forecast-section">
          <h4>Next Hour Prediction</h4>
          <div className="forecast-metrics">
            <div className="metric">
              <span className="label">Avg Usage:</span>
              <span className="value">{forecast.avg_predicted}%</span>
            </div>
            <div className="metric">
              <span className="label">Max Usage:</span>
              <span className="value">{forecast.max_predicted}%</span>
            </div>
            <div className="metric">
              <span className="label">Trend:</span>
              <span className="value">{forecast.trend === 'up' ? '📈 Up' : '📉 Down'}</span>
            </div>
            <div className="metric">
              <span className="label">Confidence:</span>
              <span className="value">{forecast.confidence}%</span>
            </div>
          </div>
        </div>
      )}

      {escalationRisk && (
        <div className={`risk-section risk-${riskColor}`}>
          <h4>Escalation Risk</h4>
          <div className="risk-metrics">
            <div className="risk-gauge">
              <div className="risk-bar" style={{ width: `${riskProb * 100}%`, backgroundColor: riskColor }}></div>
            </div>
            <div className="risk-text">
              <span className="probability">{(riskProb * 100).toFixed(1)}%</span>
              <span className="level">{riskLevel}</span>
            </div>
          </div>
          
          {riskProb < 0.4 && (
            <div className="auto-execute-badge">
              ✅ Auto-remediation available (safe to execute)
            </div>
          )}
          {riskProb >= 0.4 && riskProb < 0.7 && (
            <div className="manual-approval-badge">
              ⚠️ Manual approval required before remediation
            </div>
          )}
          {riskProb >= 0.7 && (
            <div className="alert-badge">
              🔴 High risk - manual review required
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ForecastingChart;

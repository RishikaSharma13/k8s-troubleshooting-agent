import React, { useState, useEffect } from 'react';
import '../styles/AutoRemediationStatus.css';

/**
 * AutoRemediationStatus Component
 * 
 * Displays:
 * - Last remediation action taken (restart, scale_up, alert_only)
 * - Time it was executed
 * - Before/after metrics comparison
 * - Success indicator with % improvement
 */
export default function AutoRemediationStatus({ executionId, podName, namespace }) {
  const [remediation, setRemediation] = useState(null);
  const [metricsComparison, setMetricsComparison] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!executionId || !podName) return;

    const fetchRemediationStatus = async () => {
      try {
        setLoading(true);

        // Get execution status
        const execRes = await fetch(
          `/api/v1/actions/verify?execution_id=${executionId}`
        );
        const execData = await execRes.json();

        if (execData.status === 'success') {
          // Build remediation object from execution data
          const remediationAction = {
            action: execData.action_type || 'unknown',
            status: execData.status,
            timestamp: execData.created_at,
            result: execData.verification_result,
          };

          // Extract before/after metrics from verification result
          if (execData.verification_result) {
            const comparison = {
              memory_before: execData.verification_result.metrics_before?.memory_percent || 0,
              memory_after: execData.verification_result.metrics_after?.memory_percent || 0,
              cpu_before: execData.verification_result.metrics_before?.cpu_percent || 0,
              cpu_after: execData.verification_result.metrics_after?.cpu_percent || 0,
              restart_count_before: execData.verification_result.metrics_before?.restart_count || 0,
              restart_count_after: execData.verification_result.metrics_after?.restart_count || 0,
              remediation_successful: execData.verification_result.remediation_successful,
            };
            setMetricsComparison(comparison);
          }

          setRemediation(remediationAction);
        }

        setError(null);
      } catch (err) {
        console.error('Failed to fetch remediation status:', err);
        setError('Could not load remediation status');
      } finally {
        setLoading(false);
      }
    };

    // Initial fetch
    fetchRemediationStatus();

    // Poll every 5 seconds for updates
    const interval = setInterval(fetchRemediationStatus, 5000);
    return () => clearInterval(interval);
  }, [executionId, podName]);

  if (loading && !remediation) {
    return (
      <div className="auto-remediation-status">
        <p className="loading">⏳ Loading remediation status...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="auto-remediation-status error">
        <p>⚠️ {error}</p>
      </div>
    );
  }

  if (!remediation) {
    return (
      <div className="auto-remediation-status">
        <p className="no-data">No remediation actions yet</p>
      </div>
    );
  }

  // Calculate improvement percentages
  const memoryImprovement = metricsComparison
    ? Math.round(
        ((metricsComparison.memory_before - metricsComparison.memory_after) /
          metricsComparison.memory_before) *
          100
      )
    : 0;

  const cpuImprovement = metricsComparison
    ? Math.round(
        ((metricsComparison.cpu_before - metricsComparison.cpu_after) /
          metricsComparison.cpu_before) *
          100
      )
    : 0;

  // Get action emoji and description
  const actionConfig = {
    restart: { emoji: '🔄', label: 'Pod Restarted', color: '#4CAF50' },
    scale_up: { emoji: '📈', label: 'Scaled Up', color: '#2196F3' },
    alert_only: { emoji: '🔔', label: 'Alert Sent', color: '#FF9800' },
    unknown: { emoji: '❓', label: 'Unknown Action', color: '#9E9E9E' },
  };

  const config = actionConfig[remediation.action] || actionConfig.unknown;
  const isSuccessful = metricsComparison?.remediation_successful;

  return (
    <div className="auto-remediation-status">
      <div className="status-header">
        <div className="action-icon" style={{ color: config.color }}>
          {config.emoji}
        </div>
        <div className="header-text">
          <h3>{config.label}</h3>
          <p className="timestamp">
            {remediation.timestamp
              ? new Date(remediation.timestamp).toLocaleString()
              : 'Just now'}
          </p>
        </div>
        {isSuccessful && (
          <div className="success-badge">
            ✅ Successful
          </div>
        )}
        {!isSuccessful && isSuccessful !== null && (
          <div className="failed-badge">
            ❌ Failed
          </div>
        )}
      </div>

      {metricsComparison && (
        <div className="metrics-comparison">
          <h4>Before & After Metrics</h4>

          {/* Memory Comparison */}
          <div className="metric-row">
            <div className="metric-label">
              <span>Memory Usage</span>
              {memoryImprovement > 0 && (
                <span className="improvement">
                  ↓ {memoryImprovement}%
                </span>
              )}
            </div>
            <div className="metric-bars">
              <div className="before-bar">
                <div
                  className="bar-fill memory-before"
                  style={{ width: `${metricsComparison.memory_before}%` }}
                >
                  {metricsComparison.memory_before > 10 &&
                    `${Math.round(metricsComparison.memory_before)}%`}
                </div>
              </div>
              <div className="arrow">→</div>
              <div className="after-bar">
                <div
                  className="bar-fill memory-after"
                  style={{ width: `${metricsComparison.memory_after}%` }}
                >
                  {metricsComparison.memory_after > 10 &&
                    `${Math.round(metricsComparison.memory_after)}%`}
                </div>
              </div>
            </div>
          </div>

          {/* CPU Comparison */}
          <div className="metric-row">
            <div className="metric-label">
              <span>CPU Usage</span>
              {cpuImprovement > 0 && (
                <span className="improvement">
                  ↓ {cpuImprovement}%
                </span>
              )}
            </div>
            <div className="metric-bars">
              <div className="before-bar">
                <div
                  className="bar-fill cpu-before"
                  style={{ width: `${metricsComparison.cpu_before}%` }}
                >
                  {metricsComparison.cpu_before > 10 &&
                    `${Math.round(metricsComparison.cpu_before)}%`}
                </div>
              </div>
              <div className="arrow">→</div>
              <div className="after-bar">
                <div
                  className="bar-fill cpu-after"
                  style={{ width: `${metricsComparison.cpu_after}%` }}
                >
                  {metricsComparison.cpu_after > 10 &&
                    `${Math.round(metricsComparison.cpu_after)}%`}
                </div>
              </div>
            </div>
          </div>

          {/* Restart Count */}
          {metricsComparison.restart_count_before !== undefined && (
            <div className="metric-row">
              <div className="metric-label">
                <span>Restart Count</span>
              </div>
              <div className="restart-comparison">
                <span className="restart-before">
                  Before: {metricsComparison.restart_count_before}
                </span>
                <span className="arrow">→</span>
                <span className="restart-after">
                  After: {metricsComparison.restart_count_after}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {remediation.result && (
        <div className="remediation-details">
          <p className="result-text">
            {typeof remediation.result === 'string'
              ? remediation.result
              : JSON.stringify(remediation.result, null, 2)}
          </p>
        </div>
      )}
    </div>
  );
}

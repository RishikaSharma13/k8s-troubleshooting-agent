import React, { useState, useEffect } from 'react';
import ForecastingChart from './ForecastingChart';
import AutoRemediationStatus from './AutoRemediationStatus';
import '../styles/ApprovalPanel.css';

export default function ApprovalPanel({ incident, onApprove, onReject }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [executionId, setExecutionId] = useState(null);
  const [autoRemediationTriggered, setAutoRemediationTriggered] = useState(false);

  if (!incident) {
    return (
      <div className="approval-panel">
        <p style={{ textAlign: 'center', color: '#999' }}>
          📋 Select an incident to review
        </p>
      </div>
    );
  }

  const handleApprove = async () => {
    try {
      setLoading(true);
      setError(null);

      // 1. Approve the incident
      const approveRes = await fetch(
        `/api/v1/actions/approve?incident_id=${incident.incident_id}`,
        { method: 'POST' }
      );

      if (!approveRes.ok) throw new Error('Failed to approve incident');

      // 2. Check if auto-remediation should trigger
      const autoRemRes = await fetch(
        `/api/v1/actions/auto-remediate?pod_name=${incident.pod_name}&namespace=${incident.namespace || 'default'}`,
        { method: 'POST' }
      );

      const autoRemData = await autoRemRes.json();

      // If auto-execute was triggered, save execution ID
      if (autoRemData.execution_id) {
        setExecutionId(autoRemData.execution_id);
        setAutoRemediationTriggered(true);
      }

      // 3. Notify parent
      if (onApprove) {
        onApprove(incident.incident_id);
      }

      setLoading(false);
    } catch (err) {
      console.error('Error approving incident:', err);
      setError(err.message);
      setLoading(false);
    }
  };

  const handleReject = async () => {
    try {
      setLoading(true);
      setError(null);

      const res = await fetch(
        `/api/v1/actions/reject?incident_id=${incident.incident_id}`,
        { method: 'POST' }
      );

      if (!res.ok) throw new Error('Failed to reject incident');

      if (onReject) {
        onReject(incident.incident_id);
      }

      setLoading(false);
    } catch (err) {
      console.error('Error rejecting incident:', err);
      setError(err.message);
      setLoading(false);
    }
  };

  return (
    <div className="approval-panel">
      <h2>🔍 Review & Approve</h2>

      {error && (
        <div className="error-message">
          ⚠️ {error}
        </div>
      )}

      {/* SECTION 1: Forecast & Risk Analysis */}
      <ForecastingChart
        incidentId={incident.incident_id}
        podName={incident.pod_name}
        namespace={incident.namespace || 'default'}
      />

      {/* SECTION 2: Auto-Remediation Status (if action was taken) */}
      {autoRemediationTriggered && executionId && (
        <AutoRemediationStatus
          executionId={executionId}
          podName={incident.pod_name}
          namespace={incident.namespace || 'default'}
        />
      )}

      {/* SECTION 3: Incident Details */}
      <div className="incident-details">
        <div className="detail-row">
          <label>Pod:</label>
          <span>{incident.pod_name}</span>
        </div>
        <div className="detail-row">
          <label>Namespace:</label>
          <span>{incident.namespace || 'default'}</span>
        </div>
        <div className="detail-row">
          <label>Risk:</label>
          <span className={`risk-badge risk-${incident.risk_score > 70 ? 'high' : 'medium'}`}>
            {incident.risk_score}
          </span>
        </div>
        <div className="detail-row">
          <label>Recommended Action:</label>
          <span>{incident.action_type || 'INVESTIGATE_LOGS'}</span>
        </div>
      </div>

      {/* SECTION 4: Approval Buttons */}
      <div className="action-buttons">
        <button
          className="btn btn-approve"
          onClick={handleApprove}
          disabled={loading}
        >
          {loading ? '⏳ Processing...' : '✅ Approve'}
        </button>
        <button
          className="btn btn-reject"
          onClick={handleReject}
          disabled={loading}
        >
          {loading ? '⏳ Processing...' : '❌ Reject'}
        </button>
      </div>
    </div>
  );
}

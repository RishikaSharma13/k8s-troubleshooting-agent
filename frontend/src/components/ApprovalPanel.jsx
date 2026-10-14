import { useState } from 'react'

export default function ApprovalPanel({ incident, onApprove }) {
  const [approving, setApproving] = useState(false)

  const handleApprove = async () => {
    if (!incident) return
    setApproving(true)
    try {
      const res = await fetch('http://127.0.0.1:8000/api/v1/actions/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ incident_id: incident.id })
      })
      if (res.ok) { alert('✅ Approved!'); onApprove() }
    } catch (e) { console.error(e); alert('❌ Error') }
    setApproving(false)
  }

  const handleReject = async () => {
    if (!incident) return
    setApproving(true)
    try {
      const res = await fetch('http://127.0.0.1:8000/api/v1/actions/reject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ incident_id: incident.id })
      })
      if (res.ok) { alert('❌ Rejected!'); onApprove() }
    } catch (e) { console.error(e) }
    setApproving(false)
  }

  if (!incident) return <div style={{ background: 'white', borderRadius: '8px', padding: '1.5rem', display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '200px', color: '#999' }}>👈 Select an incident</div>

  return (
    <div style={{ background: 'white', borderRadius: '8px', padding: '1.5rem', boxShadow: '0 2px 4px rgba(0,0,0,0.05)' }}>
      <h3 style={{ margin: '0 0 1rem 0' }}>🔍 Review & Approve</h3>
      <div style={{ background: '#f9f9f9', padding: '1rem', borderRadius: '6px', marginBottom: '1.5rem' }}>
        <p><strong>Pod:</strong> {incident.pod_name}</p>
        <p><strong>Namespace:</strong> {incident.namespace}</p>
        <p><strong>Risk:</strong> {incident.risk_score}%</p>
      </div>
      <div style={{ display: 'flex', gap: '1rem' }}>
        <button onClick={handleApprove} disabled={approving} style={{ flex: 1, padding: '0.75rem', background: '#27ae60', color: 'white', border: 'none', borderRadius: '6px', fontWeight: '600', cursor: 'pointer' }}>
          {approving ? '⏳' : '✅ Approve'}
        </button>
        <button onClick={handleReject} disabled={approving} style={{ flex: 1, padding: '0.75rem', background: '#e74c3c', color: 'white', border: 'none', borderRadius: '6px', fontWeight: '600', cursor: 'pointer' }}>
          {approving ? '⏳' : '❌ Reject'}
        </button>
      </div>
    </div>
  )
}

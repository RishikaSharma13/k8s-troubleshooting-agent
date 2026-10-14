export default function IncidentList({ incidents, onSelect, loading }) {
  return (
    <div style={{ background: 'white', borderRadius: '8px', padding: '1.5rem', boxShadow: '0 2px 4px rgba(0,0,0,0.05)' }}>
      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem' }}>
        <h2 style={{ margin: 0 }}>📋 Pending Incidents</h2>
        <span style={{ background: '#667eea', color: 'white', padding: '0.25rem 0.75rem', borderRadius: '20px', fontSize: '0.85rem', fontWeight: 'bold' }}>{incidents.length}</span>
      </div>
      {loading && <p>Loading...</p>}
      {incidents.length === 0 ? (
        <div style={{ textAlign: 'center', color: '#999', padding: '2rem' }}><p>✅ All systems healthy!</p></div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {incidents.map((incident) => (
            <div key={incident.id} onClick={() => onSelect(incident)} style={{ border: '1px solid #eee', borderRadius: '6px', padding: '1rem', cursor: 'pointer' }}>
              <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.5rem' }}>
                <span style={{ background: '#e74c3c', color: 'white', padding: '0.25rem 0.5rem', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 'bold' }}>{incident.severity}</span>
                <span style={{ fontWeight: 'bold' }}>{incident.pod_name}</span>
              </div>
              <p style={{ margin: '0.5rem 0', fontSize: '0.9rem', color: '#555' }}>{incident.message}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

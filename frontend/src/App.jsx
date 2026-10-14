import { useState, useEffect } from 'react'
import IncidentList from './components/IncidentList'
import ApprovalPanel from './components/ApprovalPanel'
import MetricsChart from './components/MetricsChart'

function App() {
  const [incidents, setIncidents] = useState([])
  const [selected, setSelected] = useState(null)
  const [loading, setLoading] = useState(false)

  const refresh = async () => {
    setLoading(true)
    try {
      const res = await fetch('http://127.0.0.1:8000/api/v1/risks/pending-incidents')
      const data = await res.json()
      // API returns { status, count, incidents: [...] }
      // Map incident_id to id for React keys
      const mapped = data.incidents.map(inc => ({
        ...inc,
        id: inc.incident_id,
        message: inc.recommended_action
      }))
      setIncidents(mapped)
    } catch (e) { console.error(e) }
    setLoading(false)
  }

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: '#fafafa' }}>
      <header style={{ background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', padding: '2rem' }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>🚀 K8s Reliability Agent</h1>
        <p style={{ opacity: 0.9 }}>AI-powered incident detection & remediation</p>
      </header>
      <main style={{ flex: 1, padding: '2rem', maxWidth: '1400px', margin: '0 auto', width: '100%' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
          <IncidentList incidents={incidents} onSelect={setSelected} loading={loading} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <ApprovalPanel incident={selected} onApprove={() => { refresh(); setSelected(null) }} />
            <MetricsChart />
          </div>
        </div>
      </main>
    </div>
  )
}

export default App

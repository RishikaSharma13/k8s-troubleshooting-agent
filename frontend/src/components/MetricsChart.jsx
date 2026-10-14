import { useEffect, useState } from 'react'

export default function MetricsChart() {
  const [metrics, setMetrics] = useState([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const fetch_metrics = async () => {
      setLoading(true)
      try {
        const res = await fetch('http://127.0.0.1:8000/metrics/history')
        const data = await res.json()
        setMetrics(data)
      } catch (e) { console.error(e) }
      setLoading(false)
    }
    fetch_metrics()
    const interval = setInterval(fetch_metrics, 10000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div style={{ background: 'white', borderRadius: '8px', padding: '1.5rem', boxShadow: '0 2px 4px rgba(0,0,0,0.05)' }}>
      <h3>📊 Metrics</h3>
      {loading ? <p>Loading...</p> : metrics.length === 0 ? <p>No data yet</p> : <p>{metrics.length} readings available</p>}
    </div>
  )
}

import { useState, useEffect, useRef, useCallback } from 'react'
import './index.css'

const API_BASE = 'http://localhost:8000'
const WS_URL = 'ws://localhost:8000/ws'

function formatCurrency(amount) {
  if (amount >= 100000) return `₹${(amount / 100000).toFixed(1)}L`
  if (amount >= 1000) return `₹${(amount / 1000).toFixed(1)}K`
  return `₹${Math.round(amount).toLocaleString('en-IN')}`
}

function formatRate(rate) {
  return `${(rate * 100).toFixed(1)}%`
}

const FAILURE_COLORS = {
  bank_timeout: 'blue', insufficient_funds: 'amber', card_expired: 'red',
  network_error: 'blue', auth_failed: 'amber', declined_by_bank: 'red',
  fraud_suspected: 'red', declined_by_cardholder: 'red', unknown: 'gray',
}

const STATUS_ICONS = {
  recovered: '✓', attempted_not_recovered: '✗', skipped: '—', processing: '⟳', blocked: '⊘',
}

// ── Hero Metric Card ─────────────────────────────────────────────────────────
function MetricCard({ label, value, subtitle, variant, isHero }) {
  return (
    <div className={`metric-card ${isHero ? 'hero' : ''}`}>
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${isHero ? 'hero-value' : variant || ''}`}>{value}</div>
      {subtitle && <div className="metric-subtitle">{subtitle}</div>}
    </div>
  )
}

// ── Live Feed ────────────────────────────────────────────────────────────────
function LiveFeed({ events }) {
  const feedRef = useRef(null)
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = 0
  }, [events.length])

  return (
    <div className="card" style={{ gridColumn: '1 / -1' }}>
      <div className="card-header">
        <span className="card-title">⚡ Live Recovery Feed</span>
        <span className="card-badge">{events.length} events</span>
      </div>
      <div className="card-body">
        <div className="feed-container" ref={feedRef}>
          {events.length === 0 ? (
            <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-muted)' }}>
              Click "Run Demo" to start processing failed payments
            </div>
          ) : (
            events.slice().reverse().slice(0, 50).map((evt, i) => (
              <div className="feed-item" key={i}>
                <div className={`feed-icon ${evt.recovered ? 'recovered' : evt.status === 'skipped' ? 'skipped' : 'failed'}`}>
                  {evt.recovered ? '✓' : evt.status === 'skipped' ? '—' : '✗'}
                </div>
                <div className="feed-content">
                  <div className="feed-title">
                    <span className={`tag ${FAILURE_COLORS[evt.failure_type] || 'gray'}`}>{evt.failure_type}</span>
                    {' → '}
                    <span className={`tag ${evt.recovered ? 'green' : 'gray'}`}>{evt.intervention}</span>
                  </div>
                  <div className="feed-desc">{evt.reasoning || `${evt.merchant || ''} • ${evt.failure_type}`}</div>
                </div>
                <div className={`feed-amount ${evt.recovered ? 'green' : 'red'}`}>
                  {evt.recovered ? '+' : ''}{formatCurrency(evt.amount || 0)}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

// ── Bar Chart ────────────────────────────────────────────────────────────────
function BarChart({ data, color, maxValue }) {
  const max = maxValue || Math.max(...Object.values(data), 1)
  return (
    <div className="bar-chart">
      {Object.entries(data).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([label, value]) => (
        <div className="bar-row" key={label}>
          <span className="bar-label" title={label}>{label.replace(/_/g, ' ')}</span>
          <div className="bar-track">
            <div className={`bar-fill ${color}`} style={{ width: `${Math.max(2, (value / max) * 100)}%` }} />
          </div>
          <span className="bar-value">{value}</span>
        </div>
      ))}
    </div>
  )
}

// ── Recovery Waterfall ───────────────────────────────────────────────────────
function Waterfall({ metrics }) {
  const steps = [
    { label: 'Failed', count: metrics.total_failed || 0, color: 'var(--accent-red)' },
    { label: 'Diagnosed', count: metrics.total_processed || 0, color: 'var(--accent-blue)' },
    { label: 'Attempted', count: (metrics.total_processed || 0) - (metrics.total_skipped || 0), color: 'var(--accent-amber)' },
    { label: 'Recovered', count: metrics.total_recovered || 0, color: 'var(--accent-green)' },
  ]
  return (
    <div className="waterfall">
      {steps.map((step, i) => (
        <div key={step.label} style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
          <div className="waterfall-step" style={{ flex: 1 }}>
            <div className="waterfall-count" style={{ color: step.color }}>{step.count}</div>
            <div className="waterfall-label">{step.label}</div>
          </div>
          {i < steps.length - 1 && <div className="waterfall-arrow">→</div>}
        </div>
      ))}
    </div>
  )
}

// ── Audit Trail ──────────────────────────────────────────────────────────────
function AuditTrail({ pipeline }) {
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">🔍 Audit Trail</span>
        <span className="card-badge">{pipeline.length} records</span>
      </div>
      <div className="card-body">
        <div className="feed-container" style={{ maxHeight: '300px' }}>
          {pipeline.slice().reverse().slice(0, 20).map((record, i) => (
            <div className="audit-item" key={i}>
              <span className="audit-agent">[{record.failure_type}]</span>{' '}
              <span className="audit-action">{record.intervention}</span>{' → '}
              <span className={`audit-result ${record.recovered ? '' : 'failed'}`}>
                {record.recovered ? `RECOVERED ₹${record.recovered_amount?.toFixed(0) || record.amount?.toFixed(0)}` : 'NOT RECOVERED'}
              </span>
              {' '}
              <span style={{ color: 'var(--text-muted)' }}>
                conf:{(record.confidence || 0).toFixed(2)} amt:₹{(record.amount || 0).toFixed(0)}
              </span>
            </div>
          ))}
          {pipeline.length === 0 && (
            <div style={{ padding: '20px 0', textAlign: 'center', color: 'var(--text-muted)', fontFamily: 'var(--font-sans)' }}>
              No audit records yet
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [metrics, setMetrics] = useState({
    total_processed: 0, total_failed: 0, total_recovered: 0,
    total_recovered_amount_rupees: 0, recovery_rate: 0, total_skipped: 0,
  })
  const [events, setEvents] = useState([])
  const [pipeline, setPipeline] = useState([])
  const [failureDist, setFailureDist] = useState({})
  const [interventionDist, setInterventionDist] = useState({})
  const [isRunning, setIsRunning] = useState(false)
  const [connected, setConnected] = useState(false)
  const [batchSize, setBatchSize] = useState(100)
  const wsRef = useRef(null)

  // WebSocket connection
  useEffect(() => {
    function connect() {
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        setTimeout(connect, 3000)
      }
      ws.onerror = () => ws.close()

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'init') {
            setMetrics(msg.data.metrics || {})
            setFailureDist(msg.data.failure_distribution || {})
            setInterventionDist(msg.data.intervention_distribution || {})
            if (msg.data.recent_pipeline) setPipeline(msg.data.recent_pipeline)
          }
          if (msg.type === 'recovery') {
            const d = msg.data
            setEvents(prev => [...prev, d])
            setMetrics(d.metrics || {})
          }
          if (msg.type === 'diagnosis' || msg.type === 'strategy' || msg.type === 'execution') {
            // Could show intermediate steps
          }
        } catch (err) { /* ignore parse errors */ }
      }
    }
    connect()
    return () => { if (wsRef.current) wsRef.current.close() }
  }, [])

  // Run batch simulation
  const runDemo = useCallback(async () => {
    setIsRunning(true)
    setEvents([])
    setPipeline([])
    setFailureDist({})
    setInterventionDist({})
    try {
      const res = await fetch(`${API_BASE}/api/simulate/batch?count=${batchSize}`, { method: 'POST' })
      const data = await res.json()
      if (data.metrics) setMetrics(data.metrics)
      if (data.results) setPipeline(data.results)

      // Compute distributions from results
      const fDist = {}, iDist = {}
      data.results?.forEach(r => {
        fDist[r.failure_type] = (fDist[r.failure_type] || 0) + 1
        iDist[r.intervention] = (iDist[r.intervention] || 0) + 1
      })
      setFailureDist(fDist)
      setInterventionDist(iDist)
    } catch (err) {
      console.error('Demo failed:', err)
    }
    setIsRunning(false)
  }, [batchSize])

  // Emergency stop
  const emergencyStop = async () => {
    try {
      await fetch(`${API_BASE}/api/emergency-stop?halt=true`, { method: 'POST' })
    } catch (err) { console.error(err) }
  }

  const recoveredAmount = metrics.total_recovered_amount_rupees || (metrics.verification_stats?.total_recovered_amount_rupees) || 0

  return (
    <div className="app">
      <div className="ambient-glow" />

      {/* Header */}
      <header className="header">
        <div className="header-left">
          <div>
            <div className="logo">ReviveAI</div>
            <div className="logo-sub">Autonomous Revenue Recovery</div>
          </div>
        </div>
        <div className="header-right">
          <div className={`status-badge`} style={connected ? {} : { background: 'rgba(239,68,68,0.1)', color: 'var(--accent-red)', borderColor: 'rgba(239,68,68,0.2)' }}>
            <div className="status-dot" style={connected ? {} : { background: 'var(--accent-red)' }} />
            {connected ? 'Connected' : 'Disconnected'}
          </div>
        </div>
      </header>

      <main className="main-content">
        {/* Controls */}
        <div className="controls-bar">
          <div className="controls-left">
            <button className="btn btn-primary" onClick={runDemo} disabled={isRunning}>
              {isRunning ? <><div className="spinner" /> Processing...</> : '▶ Run Demo'}
            </button>
            <select
              className="btn btn-secondary"
              value={batchSize}
              onChange={e => setBatchSize(Number(e.target.value))}
              style={{ cursor: 'pointer' }}
            >
              <option value={50}>50 transactions</option>
              <option value={100}>100 transactions</option>
              <option value={200}>200 transactions</option>
              <option value={500}>500 transactions</option>
            </select>
          </div>
          <div className="controls-right">
            <button className="btn btn-danger" onClick={emergencyStop}>
              ⊘ Emergency Stop
            </button>
          </div>
        </div>

        {/* Hero Metrics */}
        <div className="hero-metrics">
          <MetricCard
            label="Revenue Recovered"
            value={formatCurrency(recoveredAmount)}
            subtitle={`From ${metrics.total_recovered || 0} successful recoveries`}
            isHero
          />
          <MetricCard
            label="Recovery Rate"
            value={formatRate(metrics.recovery_rate || 0)}
            subtitle="Of all failed payments"
            variant="green"
          />
          <MetricCard
            label="Processed"
            value={metrics.total_processed || 0}
            subtitle={`${metrics.total_skipped || 0} correctly skipped`}
            variant="blue"
          />
          <MetricCard
            label="Automation"
            value={metrics.total_processed ? formatRate((metrics.total_processed - 0) / Math.max(1, metrics.total_failed)) : '—'}
            subtitle="Fully automated"
            variant="amber"
          />
        </div>

        {/* Recovery Waterfall */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">📊 Recovery Waterfall</span>
          </div>
          <div className="card-body">
            <Waterfall metrics={metrics} />
          </div>
        </div>

        {/* Live Feed */}
        <LiveFeed events={events} />

        {/* Charts Row */}
        <div className="dashboard-grid">
          <div className="card">
            <div className="card-header">
              <span className="card-title">🔴 Failure Distribution</span>
            </div>
            <div className="card-body">
              {Object.keys(failureDist).length > 0 ? (
                <BarChart data={failureDist} color="red" />
              ) : (
                <div style={{ padding: '30px 0', textAlign: 'center', color: 'var(--text-muted)' }}>
                  No data yet
                </div>
              )}
            </div>
          </div>
          <div className="card">
            <div className="card-header">
              <span className="card-title">🔧 Intervention Strategy Distribution</span>
            </div>
            <div className="card-body">
              {Object.keys(interventionDist).length > 0 ? (
                <BarChart data={interventionDist} color="blue" />
              ) : (
                <div style={{ padding: '30px 0', textAlign: 'center', color: 'var(--text-muted)' }}>
                  No data yet
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Audit Trail */}
        <AuditTrail pipeline={pipeline} />
      </main>
    </div>
  )
}

import { useState } from 'react'
import type { KeyboardEvent } from 'react'

interface Props {
  onScan: (universe: string) => void
  onCustom: (symbols: string[], bypassGates: boolean) => void
  loading: boolean
}

const UNIVERSE_KEY = 'swing_eligible'
const UNIVERSE_LABEL = 'Swing-eligible (~200)'
const UNIVERSE_HINT = '$500M+ mcap, 500K+ ADV — incl. high-beta movers (ASTS, RKLB, MU, IONQ, MSTR…)'

const SCORE_TIERS = [
  { range: '≥ 80',  label: 'Strong', color: '#4ade80', desc: 'Top 20% — win rate 63%, avg gain 2.7R', action: 'Take it — normal size' },
  { range: '65–79', label: 'Solid',  color: '#86efac', desc: 'Good — win rate 51%, avg gain 1.3R',    action: 'Take it — honor the stop' },
  { range: '50–64', label: 'Medium', color: '#fbbf24', desc: 'Decent — win rate 41%; modest edge',    action: 'Smaller size only' },
  { range: '35–49', label: 'Weak',   color: '#fb923c', desc: 'Below average — win rate 30%',          action: 'Skip unless special reason' },
  { range: '< 35',  label: 'Skip',   color: '#f87171', desc: 'Bottom tier — win rate 26%',            action: 'Skip' },
]

const SETUP_GUIDE = [
  {
    name: 'Breakout',
    hold: '5–10d',
    color: '#60a5fa',
    signals: 'Price breaks out of a tight 7+ day range · Volume 1.5× normal · BB squeeze releasing',
    thesis: 'The stock was coiling and just launched. Ride the expansion. Stop below the base.',
  },
  {
    name: 'Momentum',
    hold: '7–14d',
    color: '#4ade80',
    signals: 'All EMAs stacked (price > EMA8 > EMA21 > EMA50) · ADX ≥ 22 · outperforming the market',
    thesis: 'Strong trend in motion. Trail your stop behind the 8-day EMA.',
  },
  {
    name: 'Bounce',
    hold: '3–7d',
    color: '#fbbf24',
    signals: 'RSI < 30 (oversold) · price holds above EMA 200 · bullish divergence forming',
    thesis: 'Oversold within an uptrend. Quick trade — take profit early, keep a tight stop.',
  },
  {
    name: 'Retest',
    hold: '10–21d',
    color: '#a78bfa',
    signals: 'Broke out 5–20 days ago · pulled back to that level · now building a new base',
    thesis: 'Second leg of a breakout. The prior level is now support. Risk is well-defined.',
  },
]

const PLAYBOOK = [
  { n: 1, q: 'Score ≥ 65?',               a: 'Yes → take it. 65–79 = solid (51% win). ≥ 80 = strong (63% win). 50–64 = reduce size. < 50 = skip.' },
  { n: 2, q: 'Is the pattern clear?',     a: "Open the expanded row and read the drivers. If you can't see why it's a Breakout / Momentum / Bounce / Retest, skip it." },
  { n: 3, q: 'Is entry price realistic?', a: 'Green dot = near entry now. Yellow = not yet — place a limit order. "LATE ENTRY" badge = price moved too far — wait for a pullback.' },
  { n: 4, q: 'Can you afford the stop?',  a: 'Dollar risk = (Entry − Stop) × shares. Keep it under 1% of your account. Adjust share count — never widen the stop level.' },
]

export function SwingInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')
  const [symbolsText, setSymbolsText] = useState<string>('')
  const [showLegend, setShowLegend] = useState<boolean>(false)
  const [bypassGates, setBypassGates] = useState<boolean>(true)

  function parseSymbols(): string[] {
    return symbolsText
      .split(/[,\s]+/)
      .map(s => s.trim().toUpperCase())
      .filter(s => s.length > 0 && s.length <= 10)
      .slice(0, 20)
  }

  function handleScan() {
    onScan(UNIVERSE_KEY)
  }

  function handleCustom() {
    const syms = parseSymbols()
    if (syms.length === 0) return
    onCustom(syms, bypassGates)
  }

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleCustom()
    }
  }

  return (
    <div className="symbol-input-panel">
      <div className="momentum-mode-toggle">
        <button
          className={`mode-btn${mode === 'scan' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('scan')}
          disabled={loading}
        >
          Market Scan
        </button>
        <button
          className={`mode-btn${mode === 'custom' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('custom')}
          disabled={loading}
        >
          My Symbols
        </button>
      </div>

      {mode === 'scan' && (
        <div className="momentum-scan-row">
          <div className="momentum-scan-info">
            <span className="scan-desc">
              Scans <strong>{UNIVERSE_LABEL}</strong> — {UNIVERSE_HINT}
            </span>
            <span className="app-subtitle">
              Scans 200+ stocks and ranks by Signal Score. Quality filters applied automatically. Top results include AI commentary.
            </span>
          </div>
          <div className="momentum-scan-controls">
            <button
              className="btn btn-primary"
              onClick={handleScan}
              disabled={loading}
            >
              {loading ? 'Scanning…' : '⚡ Market Scan'}
            </button>
          </div>
        </div>
      )}

      {mode === 'custom' && (
        <div className="momentum-scan-row" style={{ alignItems: 'stretch' }}>
          <div className="momentum-scan-info" style={{ flex: '0 0 auto', maxWidth: 320 }}>
            <span className="scan-desc">
              Custom symbols — max 20, comma- or space-separated.
            </span>
            <span className="app-subtitle">
              {bypassGates
                ? 'Quality filters off — all symbols shown. Use the Score column to judge quality.'
                : 'Quality filters on — same standards as Market Scan (price, volume, R:R, pattern quality).'
              }
            </span>
          </div>
          <div
            className="momentum-scan-controls"
            style={{ flex: '1 1 auto', flexDirection: 'column', alignItems: 'stretch', gap: 8 }}
          >
            <textarea
              value={symbolsText}
              onChange={e => setSymbolsText(e.target.value)}
              onKeyDown={handleKey}
              placeholder="AAPL, MSFT, NVDA"
              rows={3}
              disabled={loading}
              style={{
                width: '100%',
                padding: 8,
                fontFamily: 'inherit',
                fontSize: 13,
                borderRadius: 4,
                border: '1px solid #334155',
                background: '#0f172a',
                color: 'inherit',
                resize: 'vertical',
              }}
            />
            <button
              className="btn btn-primary"
              onClick={handleCustom}
              disabled={loading || parseSymbols().length === 0}
            >
              {loading ? 'Scanning…' : `🚀 Run (${parseSymbols().length})`}
            </button>
            <button
              onClick={() => setBypassGates(v => !v)}
              disabled={loading}
              title={bypassGates
                ? 'Filters off — all symbols shown. Click to turn quality filters on.'
                : 'Filters on — same standards as Market Scan. Click to turn off.'
              }
              style={{
                padding: '4px 10px',
                borderRadius: 4,
                border: `1px solid ${bypassGates ? '#4338ca' : '#334155'}`,
                background: bypassGates ? '#1e1b4b' : '#1e293b',
                color: bypassGates ? '#a5b4fc' : '#64748b',
                cursor: 'pointer',
                fontSize: 12,
                fontWeight: 600,
                transition: 'all 0.15s',
              }}
            >
              {bypassGates ? '🔓 Filters Off' : '🔒 Filters On'}
            </button>
          </div>
        </div>
      )}

      {/* ── Score Guide toggle ── */}
      <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end' }}>
        <button
          className="link-btn"
          onClick={() => setShowLegend(v => !v)}
          style={{ background: 'transparent', border: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: 12, padding: '4px 6px' }}
        >
          {showLegend ? '▲ Score Guide' : '▼ Score Guide'}
        </button>
      </div>

      {showLegend && (
        <div className="score-legend">

          {/* ── How the score works ── */}
          <div className="score-legend-factors" style={{ borderLeft: '3px solid #4338ca', paddingLeft: 10 }}>
            <div className="score-legend-header" style={{ color: '#a5b4fc' }}>How the score works</div>
            <div style={{ fontSize: 12, color: '#cbd5e1', lineHeight: 1.6 }}>
              Each stock gets a <strong style={{ color: '#e2e8f0' }}>Signal Score from 0–100</strong> combining
              two models: a rule-based scorer (Reward/Risk + pattern quality + momentum + strength + volume)
              and a machine-learning model trained on 3,366 historical swing trades.
              Scores are normalized so the best opportunities in today's scan rank near 100.
            </div>
            <div style={{ marginTop: 6, fontSize: 12, color: '#cbd5e1', lineHeight: 1.6 }}>
              <strong style={{ color: '#e2e8f0' }}>Three columns to focus on first:</strong>{' '}
              <span style={{ color: '#4ade80' }}>Score</span> (overall quality),{' '}
              <span style={{ color: '#60a5fa' }}>Momentum</span> (is buying pressure rising?), and{' '}
              <span style={{ color: '#a78bfa' }}>Strength</span> (is price near the top of its range?).
              A strong trade setup usually has all three positive.
            </div>
          </div>

          {/* ── Score tiers ── */}
          <div className="score-legend-tiers">
            <div className="score-tier-table-header">
              <span>Score</span>
              <span>What it means</span>
              <span>Action</span>
            </div>
            {SCORE_TIERS.map(t => (
              <div key={t.range} className="score-tier">
                <span className="score-tier-range" style={{ color: t.color, fontWeight: 700 }}>{t.range}</span>
                <span className="score-tier-desc">{t.desc}</span>
                <span className="score-tier-action">{t.action}</span>
              </div>
            ))}
          </div>

          {/* ── Pattern guide ── */}
          <div className="score-legend-factors">
            <div className="score-legend-header">4 trade patterns</div>
            {SETUP_GUIDE.map(s => (
              <div key={s.name} className="score-factor-block">
                <div className="score-factor-row">
                  <span className="score-factor-name" style={{ color: s.color, fontWeight: 700, minWidth: 82 }}>{s.name}</span>
                  <span style={{ color: '#94a3b8', fontSize: 11, minWidth: 52 }}>hold {s.hold}</span>
                  <span className="score-factor-detail">{s.signals}</span>
                </div>
                <div style={{ margin: '3px 0 6px 28px', fontSize: 11, color: '#64748b', fontStyle: 'italic' }}>
                  {s.thesis}
                </div>
              </div>
            ))}
          </div>

          {/* ── Pre-trade checklist ── */}
          <div className="decision-framework">
            <div className="decision-framework-header">Before you trade — 4 questions</div>
            <ol className="decision-steps">
              {PLAYBOOK.map(s => (
                <li key={s.n} className="decision-step">
                  <span className="decision-step-num">{s.n}</span>
                  <span className="decision-step-q">{s.q}</span>
                  <span className="decision-step-a">{s.a}</span>
                </li>
              ))}
            </ol>
            <div style={{ marginTop: 8, padding: '6px 10px', background: '#0f172a', borderRadius: 5, fontSize: 11, color: '#64748b', borderLeft: '3px solid #334155' }}>
              <strong style={{ color: '#94a3b8' }}>Key rule</strong> — Decide your stop before entering. If price hits your stop, the setup is invalid. Exit without negotiation.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

import { useState, useRef, KeyboardEvent } from 'react'

const UNIVERSE_SIZE = 75  // keep in sync with backend/services/universe.py
const PRESET_BASKET = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'AMD']

const SCORE_LEGEND = [
  { factor: 'IV Rank',          weight: 15, detail: '≥ 50 = full. Reward selling expensive premium.' },
  { factor: 'IV / HV Ratio',    weight: 10, detail: '≥ 1.5× = full. IV above realized vol = premium-selling edge.' },
  { factor: 'Ann. Return',      weight: 12, detail: '≥ 25% ann = full. Yield quality.' },
  { factor: 'Prem / Distance',   weight:  8, detail: 'Premium as % of gap to strike. Higher = better compensated.' },
  { factor: 'Trend Align',      weight: 10, detail: 'Price > SMA50 > SMA200 = full. Only sell puts in uptrends.' },
  { factor: 'SMA50 Slope',      weight: 10, detail: '10-day SMA50 slope ≥ 0.5% = full. Rising trend confirmed.' },
  { factor: 'RSI(14)',           weight: 10, detail: '40–65 = full. Avoid overbought/oversold.' },
  { factor: 'Delta',             weight: 10, detail: '−0.15 to −0.30 = full. Aggressive <−0.30. Low-yield >−0.15.' },
  { factor: 'Expected Move',    weight: 10, detail: 'Strike outside 1σ move window = full. Inside = low.' },
  { factor: 'Spread %',          weight:  5, detail: '≤ 3% = full. Tight bid-ask = liquid market.' },
  { factor: 'Earnings in DTE',  weight: -15, detail: 'Hard penalty if earnings fall within the expiry window.' },
]

const SCORE_TIERS = [
  { range: '\u2265 70', label: 'Strong',   color: '#4ade80', desc: 'All signals aligned \u2014 high-quality CSP setup' },
  { range: '45\u201369', label: 'Moderate', color: '#facc15', desc: 'Most signals ok, not fully optimised' },
  { range: '< 45',  label: 'Weak',     color: '#f87171', desc: 'Poor yield, bad IV environment, or earnings risk' },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
}

export function CspInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')
  const [showLegend, setShowLegend] = useState(false)

  // Scan mode state
  const [topN, setTopN] = useState(20)
  const [scanMinDTE, setScanMinDTE] = useState(30)
  const [scanMaxDTE, setScanMaxDTE] = useState(60)

  // Custom mode state
  const [chips, setChips] = useState<string[]>([])
  const [inputValue, setInputValue] = useState('')
  const [minDTE, setMinDTE] = useState(30)
  const [maxDTE, setMaxDTE] = useState(60)
  const [dteError, setDteError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  function addSymbol(raw: string) {
    const sym = raw.trim().toUpperCase().replace(/[^A-Z0-9]/g, '')
    if (!sym || sym.length > 10) return
    if (chips.includes(sym)) return
    if (chips.length >= 20) return
    setChips(prev => [...prev, sym])
  }

  function removeChip(sym: string) {
    setChips(prev => prev.filter(s => s !== sym))
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addSymbol(inputValue)
      setInputValue('')
    } else if (e.key === 'Backspace' && inputValue === '' && chips.length > 0) {
      setChips(prev => prev.slice(0, -1))
    }
  }

  function handleBlur() {
    if (inputValue.trim()) {
      addSymbol(inputValue)
      setInputValue('')
    }
  }

  function handleScan() {
    if (scanMinDTE > scanMaxDTE) return
    onScan(topN, scanMinDTE, scanMaxDTE)
  }

  function handleCustomSubmit() {
    let err: string | null = null
    if (minDTE > maxDTE) err = 'Min DTE must be \u2264 Max DTE'
    else if (minDTE < 1 || maxDTE > 90) err = 'DTE must be between 1 and 90'
    setDteError(err)
    if (err) return

    const allSymbols = inputValue.trim()
      ? [...chips, ...inputValue.split(/[\s,]+/).filter(Boolean)]
      : chips
    const unique = [...new Set(allSymbols.map(s => s.trim().toUpperCase()).filter(Boolean))]
    if (unique.length === 0) return
    onCustom(unique.slice(0, 20), minDTE, maxDTE)
  }

  return (
    <div className="symbol-input-panel">
      {/* Mode toggle */}
      <div className="momentum-mode-toggle">
        <button
          className={`mode-btn${mode === 'scan' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('scan')}
          disabled={loading}
        >
          ⚡ Auto Scan
        </button>
        <button
          className={`mode-btn${mode === 'custom' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('custom')}
          disabled={loading}
        >
          Custom Symbols
        </button>
        <button
          className="mode-btn score-legend-toggle"
          onClick={() => setShowLegend(v => !v)}
          title="How the CSP score is calculated"
        >
          {showLegend ? '▲ Score Guide' : '▼ Score Guide'}
        </button>
      </div>

      {showLegend && (
        <div className="score-legend">
          <div className="score-legend-tiers">
            {SCORE_TIERS.map(t => (
              <div key={t.range} className="score-tier">
                <span className="score-tier-badge" style={{ color: t.color }}>{t.label}</span>
                <span className="score-tier-range">{t.range}</span>
                <span className="score-tier-desc">{t.desc}</span>
              </div>
            ))}
          </div>
          <div className="score-legend-factors">
            <div className="score-legend-header">Score breakdown (total 100 pts)</div>
            {SCORE_LEGEND.map(f => (
              <div key={f.factor} className="score-factor-row">
                <span className="score-factor-name">{f.factor}</span>
                <span
                  className="score-factor-weight"
                  style={{ color: f.weight < 0 ? '#f87171' : '#4ade80' }}
                >
                  {f.weight > 0 ? `+${f.weight}` : f.weight} pts
                </span>
                <span className="score-factor-detail">{f.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {mode === 'scan' && (
        <div className="momentum-scan-row">
          <div className="momentum-scan-info">
            <span className="scan-desc">
              Scans <strong>{UNIVERSE_SIZE}</strong> stocks across AI · Semis · Cloud · Fintech · Growth
            </span>
            <span className="app-subtitle">Ranked by CSP composite score — returns top candidates automatically</span>
          </div>
          <div className="momentum-scan-controls">
            <label className="filter-item">
              Top
              <input
                type="number"
                className="filter-number"
                value={topN}
                min={5}
                max={50}
                step={5}
                onChange={e => setTopN(Number(e.target.value))}
                disabled={loading}
              />
              results
            </label>
            <label className="filter-item">
              Min DTE
              <input
                type="number"
                className="dte-input"
                value={scanMinDTE}
                min={1}
                max={90}
                onChange={e => setScanMinDTE(Number(e.target.value))}
                disabled={loading}
              />
            </label>
            <label className="filter-item">
              Max DTE
              <input
                type="number"
                className="dte-input"
                value={scanMaxDTE}
                min={1}
                max={90}
                onChange={e => setScanMaxDTE(Number(e.target.value))}
                disabled={loading}
              />
            </label>
            <button
              className="btn btn-primary"
              onClick={handleScan}
              disabled={loading || scanMinDTE > scanMaxDTE}
            >
              {loading ? 'Scanning…' : '⚡ Scan Now'}
            </button>
          </div>
        </div>
      )}

      {mode === 'custom' && (
        <>
          <div className="symbol-input-row">
            <div
              className="chip-container"
              onClick={() => inputRef.current?.focus()}
            >
              {chips.map(sym => (
                <span key={sym} className="chip">
                  {sym}
                  <button
                    className="chip-remove"
                    onClick={e => { e.stopPropagation(); removeChip(sym) }}
                    aria-label={`Remove ${sym}`}
                  >
                    ×
                  </button>
                </span>
              ))}
              <input
                ref={inputRef}
                className="chip-input"
                value={inputValue}
                onChange={e => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                onBlur={handleBlur}
                placeholder={chips.length === 0 ? 'Type symbols (e.g. AAPL, MSFT)…' : ''}
                disabled={loading}
              />
            </div>

            <div className="dte-controls">
              <label>
                Min DTE
                <input
                  type="number"
                  className="dte-input"
                  value={minDTE}
                  min={1}
                  max={90}
                  onChange={e => setMinDTE(Number(e.target.value))}
                  disabled={loading}
                />
              </label>
              <label>
                Max DTE
                <input
                  type="number"
                  className="dte-input"
                  value={maxDTE}
                  min={1}
                  max={90}
                  onChange={e => setMaxDTE(Number(e.target.value))}
                  disabled={loading}
                />
              </label>
            </div>

            <button
              className="btn btn-secondary"
              onClick={() => setChips(PRESET_BASKET)}
              disabled={loading}
            >
              Load Preset
            </button>
            <button
              className="btn btn-primary"
              onClick={handleCustomSubmit}
              disabled={loading || (chips.length === 0 && !inputValue.trim())}
            >
              {loading ? 'Running…' : 'Run Screener'}
            </button>
          </div>
          {dteError && <div className="dte-error">{dteError}</div>}
        </>
      )}
    </div>
  )
}

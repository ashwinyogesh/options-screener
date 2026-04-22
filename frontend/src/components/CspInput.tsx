import { useState, useRef, KeyboardEvent } from 'react'

const UNIVERSE_SIZE = 75  // keep in sync with backend/services/universe.py
const PRESET_BASKET = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'AMD']

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.4) —', weight: null, detail: '', formula: '' },
  { factor: 'IV Rank',         weight: 25,  detail: '<20=0 · 20–40 linear→8 · 40–60→15 · 60–80→21 · ≥80=25.',
    formula: 'Uses 30-day rolling HV as IV proxy.\n  iv_rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100\n  HV = std(log(Closeₜ / Closeₜ₋₁), 30d) × √252' },
  { factor: 'IV / HV Ratio',   weight: 20,  detail: '<0.9=0 · 0.9–1.1→5 · 1.1–1.4→10 · 1.4–1.7→16 · ≥1.7=20.',
    formula: 'iv_hv_ratio = yfinance_IV / HV_30d\n  yfinance IV = impliedVolatility from options chain\n  Falls back to HV if IV < 15% (stale market-closed data)' },
  { factor: 'SMA Alignment',   weight: 15,  detail: 'Price>SMA50>SMA200=15 · Price>SMA50=9 · SMA50>SMA200=5.',
    formula: 'SMA50  = rolling mean of Close over last 50 days\n  SMA200 = rolling mean of Close over last 200 days\n  Categorical: checks price > SMA50 and SMA50 > SMA200' },
  { factor: '52W High Dist.',  weight: 15,  detail: '≤5%=15 · ≤10%→11 · ≤20%→7 · ≤30%→3 · >30%=0.',
    formula: 'dist = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100\n  Negative value = below 52W high (e.g. −10 = 10% below)\n  pct_below = abs(min(dist, 0))' },
  { factor: 'RSI(14)',          weight: 10,  detail: '42–62=10 · 35–42 or 62–70 linear→6 · <35 or >70=2.',
    formula: 'Wilder-smoothed RSI(14)\n  delta = Close.diff()\n  avg_gain = EWM(alpha=1/14) of gains\n  avg_loss = EWM(alpha=1/14) of losses\n  RSI = 100 − 100 / (1 + avg_gain / avg_loss)' },
  { factor: 'Chain Median OI', weight: 15,  detail: '≥2000=15 · ≥800→11 · ≥300→7 · ≥100→3 · <100=0.',
    formula: 'chain_median_oi = median(puts_df["openInterest"])\n  Stock-level signal — median OI across all put strikes\n  for this expiration. Measures chain liquidity, not per-strike.' },
  { factor: 'Earnings in DTE', weight: -15, detail: 'Hard penalty if earnings fall within the expiry window.',
    formula: 'earnings_within_dte = True if:\n  0 ≤ (earnings_date − today).days ≤ DTE\n  Source: yfinance calendarEvents.earnings' },
  { factor: '— STRIKE SCORE (×0.6) —', weight: null, detail: '', formula: '' },
  { factor: 'Delta',            weight: 20,  detail: '−0.20→−0.25=20 · ±1 band=13 · −0.10→−0.15=7 · <−0.30=8.',
    formula: 'Black-Scholes put delta:\n  d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\n  delta = N(d1) − 1\n  σ = yfinance IV; falls back to HV_30d if IV < 15%' },
  { factor: 'Dist vs Support', weight: 20,  detail: 'Strike ≤ support=20 · 0–5% above→12 · 5–10%→5 · >10%=0.',
    formula: 'Volume Profile support levels (top-3 by cumulative volume):\n  typical_price = (High + Low + Close) / 3\n  Bins 252d of typical prices into 50 equal-width buckets\n  Sums volume per bucket; takes top-3 below current price\n  Uses nearest support level below the strike' },
  { factor: 'Exp Move Buffer', weight: 20,  detail: '>1.2σ outside=20 · 1.0–1.2σ→14 · 0.9–1.0σ→6 · inside=0.',
    formula: 'Expected move (1σ range):\n  EM = S × σ × √T    where T = DTE/365\n  EM_lower = S − EM\n  sigmas_outside = (EM_lower − strike) / EM\n  Positive = strike is outside the 1σ floor' },
  { factor: '% OTM from Spot', weight: 15,  detail: '≥15%=15 · ≥10%→11 · ≥5%→7 · ≥2%→3 · <2%=0.',
    formula: 'otm_pct = (S − K) / S × 100\n  Raw distance cushion from current price to strike\n  Independent of delta (delta also uses σ and T)' },
  { factor: 'Bid-Ask Spread',  weight: 15,  detail: '≤1%=15 · ≤3%→10 · ≤5%→6 · ≤8%→2 · >8%=0.',
    formula: 'spread_pct = (ask − bid) / mid × 100\n  where mid = (bid + ask) / 2\n  Per-strike bid/ask from yfinance options chain' },
  { factor: 'OI / Volume',      weight: 10,  detail: '≥1000=10 · ≥500→7 · ≥200→4 · ≥100→1 · <100=0.',
    formula: 'Uses volume if US market is open (9:30–16:00 ET weekday)\n  Otherwise uses openInterest at this specific strike\n  Source: yfinance options chain row for the strike' },
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
  const [expandedFactor, setExpandedFactor] = useState<string | null>(null)

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
            <div className="score-legend-header">Score breakdown — Final = 0.4 × Env + 0.6 × Strike</div>
            {SCORE_LEGEND.map(f => (
              f.weight === null
                ? <div key={f.factor} className="score-factor-section">{f.factor}</div>
                : <div key={f.factor} className="score-factor-block">
                    <div
                      className="score-factor-row score-factor-row-clickable"
                      onClick={() => setExpandedFactor(expandedFactor === f.factor ? null : f.factor)}
                      title="Click to show calculation"
                    >
                      <span className="score-factor-expand">
                        {expandedFactor === f.factor ? '▾' : '▸'}
                      </span>
                      <span className="score-factor-name">{f.factor}</span>
                      <span
                        className="score-factor-weight"
                        style={{ color: f.weight < 0 ? '#f87171' : '#4ade80' }}
                      >
                        {f.weight > 0 ? `+${f.weight}` : f.weight} pts
                      </span>
                      <span className="score-factor-detail">{f.detail}</span>
                    </div>
                    {expandedFactor === f.factor && f.formula && (
                      <pre className="score-factor-formula">{f.formula}</pre>
                    )}
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

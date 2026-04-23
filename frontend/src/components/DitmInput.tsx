import { useState, useRef } from 'react'
import type { KeyboardEvent } from 'react'

const UNIVERSE_SIZE = 75

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.35) —', weight: null, detail: '', why: '', formula: '' },
  { factor: 'IV Rank (inverted)',   weight: 25, detail: '<20=25 · 20–40 linear→15 · 40–60→7 · 60–80→2 · ≥80=0.',
    why: 'You are BUYING options — low IV means cheap premium, less extrinsic you overpay. High IV rank means options are historically expensive; buying inflated premium destroys edge.',
    formula: 'IV Rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100\n  INVERTED: low rank = high score\n  HV = std(log(Closeₜ / Closeₜ₋₁), 30d) × √252' },
  { factor: 'IV / HV Ratio (inv.)', weight: 20, detail: '<0.8=20 · 0.8–1.0→12 · 1.0–1.3→5 · 1.3–1.6→1 · ≥1.6=0.',
    why: 'IV < HV means the options market is pricing in LESS movement than the stock actually delivers. You are getting a discount relative to realized volatility — the buyer\'s edge.',
    formula: 'iv_hv_ratio = yfinance_IV / HV_30d\n  INVERTED: ratio < 1.0 = IV cheaper than realized vol' },
  { factor: 'SMA Alignment',        weight: 20, detail: 'Price>SMA50>SMA200=20 · Price>SMA50=12 · SMA50>SMA200=6.',
    why: 'DITM calls are long-delta positions — you need an uptrend to profit. A full SMA alignment (price > SMA50 > SMA200) confirms the multi-timeframe uptrend is intact before buying leveraged exposure.',
    formula: 'SMA50  = rolling mean of Close over last 50 days\n  SMA200 = rolling mean of Close over last 200 days\n  Categorical: price > SMA50 and SMA50 > SMA200' },
  { factor: '52W High Dist.',       weight: 15, detail: '≤5%=15 · ≤10%→11 · ≤20%→4 · ≤30%→0 · >30%=0.',
    why: 'Stocks near their highs are in momentum; they tend to continue higher. A DITM call on a stock 40% below its 52-week high has no momentum tailwind and large downside exposure.',
    formula: 'dist = (Close − max(Close, 252d)) / max(Close, 252d) × 100\n  pct_below = abs(min(dist, 0))' },
  { factor: 'RSI(14)',              weight: 10, detail: '45–68=10 · 68–78 linear→4 · 35–45 linear→10 · 30–35=2 · <30 or >78=0.',
    why: 'RSI 45–68 = sustained uptrend momentum without being dangerously overbought. Buying a call on an overbought stock risks a sharp pullback immediately reducing the option\'s delta.',
    formula: 'Wilder-smoothed RSI(14)\n  Asymmetric: downtrend (<30) disqualifies; overbought (>78) penalized' },
  { factor: 'Chain Median OI',      weight: 10, detail: 'log₁₀ scale · log₁₀(OI)/log₁₀(5000) × 10.',
    why: 'Deep ITM calls are illiquid by nature. Minimum chain OI confirms a real market exists, enabling a fair entry and an exit when you want to close or roll the position.',
    formula: 'Filters to 0.65 < delta < 0.95 (DITM call range)\n  chain_median_oi = np.median([oi for candidates])\n  pts = min(log10(OI) / log10(5000), 1.0) × 10' },
  { factor: 'Earnings in DTE',      weight: -15, detail: 'Hard penalty if earnings fall within the expiry window.',
    why: 'Earnings create gap risk. A gap-down destroys intrinsic value immediately. A gap-up is good but you likely overpaid for IV going in. The risk-reward of holding through earnings on a leveraged position is unfavorable.',
    formula: 'earnings_within_dte = True if:\n  0 ≤ (earnings_date − today).days ≤ DTE' },
  { factor: '— STRIKE SCORE (×0.65) —', weight: null, detail: '', why: '', formula: '' },
  { factor: 'Delta',                weight: 30, detail: '0.80–0.85=30 · ±band=24 · further out=15/8 · <0.65=0.',
    why: 'Delta 0.80–0.85 is the DITM sweet spot: 80–85% correlation to stock movement (near stock substitute), while still paying less than 100% of the stock price. Higher delta = deeper but lower leverage. Lower delta = more extrinsic time premium paid.',
    formula: 'Black-Scholes call delta:\n  d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\n  call_delta = N(d1)\n  σ = yfinance IV; falls back to HV_30d if IV < 15%' },
  { factor: 'Extrinsic %',          weight: 30, detail: '≤1%=30 · ≤2%→22 · ≤4%→12 · ≤6%→4 · ≤9%→0 · >9%=0.',
    why: 'Extrinsic value is the time premium you pay that will DECAY to zero by expiration regardless of stock direction. Every dollar of extrinsic is a sunk cost. Minimizing extrinsic % is the core efficiency metric of DITM buying.',
    formula: 'intrinsic = max(0, price − strike)\n  extrinsic = max(0, premium − intrinsic)\n  extrinsic_pct = extrinsic / stock_price × 100\n  NOT extrinsic / premium — normalizes across different stock prices' },
  { factor: 'Moneyness %',          weight: 15, detail: '≥15%=15 · ≥10%→11 · ≥7%→7 · ≥4%→3 · ≥1%→0.',
    why: 'Moneyness % = how far ITM the strike is relative to current price. Deeper ITM = more intrinsic, less extrinsic ratio, better stock substitution. Shallow ITM calls still behave partly like speculative options.',
    formula: 'moneyness_pct = (price − strike) / price × 100\n  A 10% moneyness means strike is 10% below current price\n  More moneyness = more intrinsic, lower extrinsic ratio' },
  { factor: 'Bid-Ask Spread',       weight: 15, detail: '≤1%=15 · ≤3%→10 · ≤5%→5 · ≤8%→1 · >12%=0.',
    why: 'Deep ITM calls are notoriously illiquid — spreads of 5–15% on the premium are common. A wide spread on entry costs you immediately and makes exit even worse. For a position you may hold for months, spread quality compounds in importance.',
    formula: 'spread_pct = (ask − bid) / mid × 100\n  Per-strike bid/ask from yfinance call chain' },
  { factor: 'OI / Volume',          weight: 10, detail: '≥500=10 · ≥200→6 · ≥100→3 · ≥50→1 · <50=0.',
    why: 'Open interest at this specific deep strike. Low OI on deep ITM calls means you may be the only participant. Closing a position at mid becomes difficult — you face the full spread on exit.',
    formula: 'Uses volume if US market is open (9:30–16:00 ET weekday)\n  Otherwise uses openInterest at this specific call strike' },
]

const SCORE_TIERS = [
  { range: '≥ 70', label: 'Strong',   color: '#4ade80', desc: 'Cheap IV environment + efficient deep strike + liquid chain — high-conviction DITM entry' },
  { range: '45–69', label: 'Moderate', color: '#facc15', desc: 'Acceptable setup; some IV cost or spread friction — manageable with good execution' },
  { range: '< 45',  label: 'Weak',     color: '#f87171', desc: 'Expensive options, high extrinsic, poor liquidity, or no uptrend — avoid' },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
}

export function DitmInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')
  const [showLegend, setShowLegend] = useState(false)
  const [expandedFactor, setExpandedFactor] = useState<string | null>(null)

  const [topN, setTopN] = useState(15)
  const [scanMinDTE, setScanMinDTE] = useState(90)
  const [scanMaxDTE, setScanMaxDTE] = useState(210)

  const [chips, setChips] = useState<string[]>([])
  const [inputValue, setInputValue] = useState('')
  const [minDTE, setMinDTE] = useState(90)
  const [maxDTE, setMaxDTE] = useState(210)
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
    if (minDTE > maxDTE) err = 'Min DTE must be ≤ Max DTE'
    else if (minDTE < 1 || maxDTE > 730) err = 'DTE must be between 1 and 730'
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
          title="How the DITM score is calculated"
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
            <div className="score-legend-header">Score breakdown — Final = 0.35 × Env + 0.65 × Strike</div>
            {SCORE_LEGEND.map(f => (
              f.weight === null
                ? <div key={f.factor} className="score-factor-section">{f.factor}</div>
                : <div key={f.factor} className="score-factor-block">
                    <div
                      className="score-factor-row score-factor-row-clickable"
                      onClick={() => setExpandedFactor(expandedFactor === f.factor ? null : f.factor)}
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
                    {expandedFactor === f.factor && (f.why || f.formula) && (
                      <div className="score-factor-expanded">
                        {f.why && <p className="score-factor-why">{f.why}</p>}
                        {f.formula && <pre className="score-factor-formula">{f.formula}</pre>}
                      </div>
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
            <span className="app-subtitle">Ranked by DITM composite score — best cheap, deep, liquid calls</span>
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
              <input type="number" className="dte-input" value={scanMinDTE}
                min={1} max={730} onChange={e => setScanMinDTE(Number(e.target.value))} disabled={loading} />
            </label>
            <label className="filter-item">
              Max DTE
              <input type="number" className="dte-input" value={scanMaxDTE}
                min={1} max={730} onChange={e => setScanMaxDTE(Number(e.target.value))} disabled={loading} />
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
            <div className="chip-container" onClick={() => inputRef.current?.focus()}>
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
                <input type="number" className="dte-input" value={minDTE}
                  min={1} max={730} onChange={e => setMinDTE(Number(e.target.value))} />
              </label>
              <label>
                Max DTE
                <input type="number" className="dte-input" value={maxDTE}
                  min={1} max={730} onChange={e => setMaxDTE(Number(e.target.value))} />
              </label>
            </div>
            <button
              className="btn btn-primary"
              onClick={handleCustomSubmit}
              disabled={loading || chips.length === 0}
            >
              {loading ? 'Fetching…' : 'Run Screener'}
            </button>
          </div>
          {dteError && <div className="dte-error">{dteError}</div>}
        </>
      )}
    </div>
  )
}

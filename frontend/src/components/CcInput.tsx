import { useState, useRef } from 'react'
import type { KeyboardEvent } from 'react'

const UNIVERSE_SIZE = 75

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.4) —', weight: null, detail: '', why: '', formula: '' },
  { factor: 'IV Rank',         weight: 25,  detail: '<20=0 · 20–40 linear→8 · 40–60→15 · 60–80→21 · ≥80=25.',
    why: 'Sell premium when options are historically expensive. High IV rank = inflated call prices → more premium collected for the same risk. This is the primary edge in premium selling.',
    formula: 'Uses 30-day rolling HV as IV proxy.\n  iv_rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100\n  HV = std(log(Closeₜ / Closeₜ₋₁), 30d) × √252' },
  { factor: 'IV / HV Ratio',   weight: 20,  detail: '<0.9=0 · 0.9–1.1→5 · 1.1–1.4→10 · 1.4–1.7→16 · ≥1.7=20.',
    why: "IV > HV means the market is pricing in more movement than the stock actually makes — the seller's edge. IV < HV = options are cheap; you'd be giving away premium below fair value.",
    formula: 'iv_hv_ratio = yfinance_IV / HV_30d\n  Falls back to HV if IV < 15% (stale market-closed data)' },
  { factor: 'SMA Alignment',   weight: 15,  detail: 'Price>SMA50>SMA200=15 · Price>SMA50=9 · SMA50>SMA200=5.',
    why: 'An established uptrend means the underlying stock you own retains value while you collect call premium. Stocks in uptrends are less likely to collapse, protecting the shares you hold.',
    formula: 'SMA50  = rolling mean of Close over last 50 days\n  SMA200 = rolling mean of Close over last 200 days\n  Categorical: checks price > SMA50 and SMA50 > SMA200' },
  { factor: '52W High Dist.',  weight: 15,  detail: '≤5%=15 · ≤10%→11 · ≤20%→7 · ≤30%→3 · >30%=0.',
    why: 'Stocks near their highs have strong momentum and lower downside risk on the underlying you hold. For a CC, your concern is not the stock going up — it is the stock collapsing while you are locked in a call position.',
    formula: 'dist = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100\n  pct_below = abs(min(dist, 0))' },
  { factor: 'RSI(14)',          weight: 10,  detail: '42–62=10 · 35–42 linear→6 · 62–75 linear→0 · 30–35=2 · <30 or >75=0.',
    why: 'Neutral-to-moderate RSI = steady trend without sharp reversal risk. Very overbought stocks might pull back sharply, damaging your underlying position while the premium provides only limited offset.',
    formula: 'Wilder-smoothed RSI(14)\n  Smooth decay 62→75: pts = 10 × (75 − RSI) / 13' },
  { factor: 'Chain Median OI', weight: 15,  detail: 'log₁₀ scale · log₁₀(OI)/log₁₀(5000) × 15 · capped at 15.',
    why: 'Thin chains mean wide spreads on entry and difficulty rolling if the stock moves against you. Liquid chains = trade near fair value, clean exits, and rolling to a later expiry without hunting for a counterparty.',
    formula: 'Filters candidates to 0.1 < delta < 0.4 first (call chain).\n  chain_median_oi = np.median([oi for candidates if 0.1 < delta < 0.4])\n  pts = min(log10(OI) / log10(5000), 1.0) × 15' },
  { factor: 'Earnings in DTE', weight: -15, detail: 'Hard penalty if earnings fall within the expiry window.',
    why: 'Earnings create gap risk in both directions. A post-earnings surge can call your shares away; a collapse damages your underlying. Avoid unless you specifically want to sell a call ahead of earnings.',
    formula: 'earnings_within_dte = True if:\n  0 ≤ (earnings_date − today).days ≤ DTE' },
  { factor: '— STRIKE SCORE (×0.6) —', weight: null, detail: '', why: '', formula: '' },
  { factor: 'Delta',            weight: 18,  detail: '+0.20→+0.25=18 · ±1 band=12 · +0.10→+0.15=6 · >+0.30=7.',
    why: 'Call delta approximates the probability of expiring in-the-money (stock being called away). +0.20–+0.25 ≈ 20–25% assignment chance — the sweet spot for premium vs. keeping your shares. Higher delta = more premium but higher chance of losing the position.',
    formula: 'Black-Scholes call delta:\n  d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\n  call_delta = N(d1)\n  σ = yfinance IV; falls back to HV_30d if IV < 15%' },
  { factor: 'Dist vs Resistance', weight: 13,  detail: 'Strike ≥ nearest resistance=13 · 0–5% below→8 · 5–10%→0 · >10%=0.',
    why: 'A resistance level between current price and your strike means the stock faces a ceiling before reaching your strike. Stocks frequently stall or reverse at resistance, reducing the chance of being called away.',
    formula: 'Volume Profile resistance levels (top-3 by cumulative volume ABOVE current price):\n  typical_price = (High + Low + Close) / 3\n  Bins 252d into 50 buckets; takes top-3 bins above current price\n  nearest_R = min(resistances above current price)\n  gap_pct = (nearest_R − strike) / strike × 100\n  gap ≤ 0 (strike above resistance) = 13 pts' },
  { factor: 'Exp Move Buffer', weight: 15,  detail: '≥0.2σ above ceiling=15 · 0–0.2σ→10 · −0.1–0σ→4 · deeper inside=0.',
    why: 'Selling above the 1σ upward expected move gives >68% theoretical probability the stock stays below your strike. Every 0.1σ of additional buffer above the ceiling directly improves the statistical edge at that strike.',
    formula: 'Expected move (1σ upside):\n  EM = S × σ × √T    where T = DTE/365\n  EM_upper = S + EM\n  sigmas_outside = (strike − EM_upper) / EM\n  Positive = strike is above the 1σ ceiling' },
  { factor: '% OTM from Spot', weight: 12,  detail: '≥15%=12 · ≥10%→9 · ≥5%→6 · ≥2%→2 · <2%=0.',
    why: 'Raw distance above current price before assignment risk begins. More room before the stock reaches your strike is a concrete margin of safety independent of IV or time.',
    formula: 'otm_pct = (K − S) / S × 100\n  Raw distance cushion from current price to strike\n  Independent of delta (delta also uses σ and T)' },
  { factor: 'Bid-Ask Spread',  weight: 22,  detail: '≤1%=22 · ≤3%→15 · ≤5%→8 · ≤8%→2 · >8%=0.',
    why: 'Wide spreads directly erode realized premium. A 10% spread on a $1.00 call loses $0.05–$0.10 on entry alone, and you pay it again on every roll. Execution quality determines what you actually collect vs. what the screen shows.',
    formula: 'spread_pct = (ask − bid) / mid × 100\n  Per-strike bid/ask from yfinance call options chain' },
  { factor: 'OI / Volume',      weight: 20,  detail: '≥1000=20 · ≥500→14 · ≥200→8 · ≥100→0 · <100=0.',
    why: 'High OI/volume at this specific strike = efficient price discovery, fast fills near mid, and a liquid exit if the stock surges toward your strike. Low OI = you may be the only participant, making rolling or closing costly.',
    formula: 'Uses volume if US market is open (9:30–16:00 ET weekday)\n  Otherwise uses openInterest at this specific call strike' },
]

const SCORE_TIERS = [
  { range: '≥ 70', label: 'Strong',   color: '#4ade80', desc: 'All signals aligned and chain is liquid — high-quality, executable CC setup' },
  { range: '45–69', label: 'Moderate', color: '#facc15', desc: 'Most signals ok; some weakness in environment or execution quality' },
  { range: '< 45',  label: 'Weak',     color: '#f87171', desc: 'Poor IV environment, execution risk, earnings overlap, or illiquid chain' },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
}

export function CcInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')
  const [showLegend, setShowLegend] = useState(false)
  const [expandedFactor, setExpandedFactor] = useState<string | null>(null)

  const [topN, setTopN] = useState(20)
  const [scanMinDTE, setScanMinDTE] = useState(30)
  const [scanMaxDTE, setScanMaxDTE] = useState(60)

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
    if (minDTE > maxDTE) err = 'Min DTE must be ≤ Max DTE'
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
          title="How the CC score is calculated"
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
            <span className="app-subtitle">Ranked by CC composite score — returns top candidates automatically</span>
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
                min={1} max={90} onChange={e => setScanMinDTE(Number(e.target.value))} disabled={loading} />
            </label>
            <label className="filter-item">
              Max DTE
              <input type="number" className="dte-input" value={scanMaxDTE}
                min={1} max={90} onChange={e => setScanMaxDTE(Number(e.target.value))} disabled={loading} />
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
                  min={1} max={90} onChange={e => setMinDTE(Number(e.target.value))} />
              </label>
              <label>
                Max DTE
                <input type="number" className="dte-input" value={maxDTE}
                  min={1} max={90} onChange={e => setMaxDTE(Number(e.target.value))} />
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

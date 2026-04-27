import { useState, useRef, KeyboardEvent } from 'react'
import { UNIVERSE_OPTIONS, DEFAULT_UNIVERSE, universeSize, type UniverseKey } from '../constants/universes'

const PRESET_BASKET = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'AMD']

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.4) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'HV Rank',         weight: 22,  detail: '<20=0 · 20–40 linear→6.6 · 40–60→13.2 · 60–80→18.3 · ≥80=22.',
    definition: 'A percentile (0–100) showing where today\'s 30-day historical volatility sits within its 252-day range. 100 = highest HV of the past year; 0 = lowest. Note: this is HV-derived, used as an IV proxy until true ATM IV history is available.',
    why: 'Sell premium when realized vol has been historically elevated — that condition typically coincides with rich option premium. High HV rank = expensive options → more credit collected for the same structural risk.',
    formula: 'Uses 30-day rolling HV ranked over 252 days.\n  hv_rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100\n  HV = std(log(Closeₜ / Closeₜ₋₁), 30d) × √252' },
  { factor: 'IV / HV Ratio',   weight: 28,  detail: '<0.8=0 · 0.8–0.9→2.8 · 0.9–1.1→6.7 · 1.1–1.4→14 · 1.4–1.7→22.4 · ≥1.7=28. Stale IV (NaN or ≤0.01) → 0 + flag.',
    definition: 'Implied Volatility divided by 30-day realized (Historical) Volatility. Measures whether options are priced rich or cheap relative to actual recent movement in the stock.',
    why: "IV > HV means the market is pricing in more movement than the stock actually makes — the seller's edge. IV < HV = options are cheap; you'd be giving away premium below fair value.",
    formula: 'iv_hv_ratio = yfinance_IV / HV_30d\n  yfinance IV = impliedVolatility from options chain\n  Stale-IV trigger: IV is NaN or ≤ 0.01 → IV/HV pts = 0 and row is flagged (was: silent fallback to 1.0)' },
  { factor: 'SMA Alignment',   weight: 15,  detail: 'Price>SMA50>SMA200=15 · Price>SMA50=9 · SMA50>SMA200=5.',
    definition: 'The relative ordering of price vs. its 50-day and 200-day Simple Moving Averages. All three in sequence (price > SMA50 > SMA200) is the textbook definition of a sustained uptrend.',
    why: 'A bullish trend reduces the chance the stock sells off through your strike. Price > SMA50 > SMA200 = sustained uptrend with institutional support — the lowest assignment-risk environment for a CSP.',
    formula: 'SMA50  = rolling mean of Close over last 50 days\n  SMA200 = rolling mean of Close over last 200 days\n  Categorical: checks price > SMA50 and SMA50 > SMA200' },
  { factor: '52W High Dist.',  weight: 10,  detail: 'CSP curve: ≤5%=10 · 5–10%→7.3→4.7 · 10–20%→4.7→2 · 20–30%→2→0 · >30%=0.',
    definition: 'How far the current price is below its 52-week (252-trading-day) high, expressed as a percentage. Zero means the stock is at its high; −15 means it is 15% below.',
    why: 'For CSPs: stocks near their highs have upward momentum and are less likely to gap down through your strike. Far below the 52W high signals a downtrend — puts sold there carry much higher assignment risk.',
    formula: 'dist = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100\n  Negative = below 52W high (e.g. −10 = 10% below)\n  pct_below = abs(min(dist, 0))\n  CSP curve rescaled from 15 → 10 (×10/15 throughout)' },
  { factor: 'RSI(14)',          weight: 10,  detail: 'CSP: 42–62=10 · 35–42 linear→6 · 62–75 linear→0 · 30–35=2 · <30 or >75=0.',
    definition: 'Relative Strength Index: a momentum oscillator (0–100) measuring the magnitude of recent gains vs. losses over the last 14 trading sessions. Above 70 = overbought; below 30 = oversold.',
    why: 'Mid-range RSI = healthy trend, neither overheated nor breaking down. Overbought (>75) risks a near-term reversal into your strike; deeply oversold (<30) stocks rarely recover meaningfully within the DTE window.',
    formula: 'Wilder-smoothed RSI(14)\n  delta = Close.diff()\n  avg_gain = EWM(alpha=1/14) of gains\n  avg_loss = EWM(alpha=1/14) of losses\n  RSI = 100 − 100 / (1 + avg_gain / avg_loss)\n  Smooth decay 62→75: pts = 10 × (75 − RSI) / 13' },
  { factor: 'Chain Median OI', weight: 8,   detail: 'Circuit-breaker · log₁₀(OI)/log₁₀(5000) × 8 · near-always maxed on liquid tickers; bumped from 5 to give small-caps more teeth.',
    definition: 'The median open interest across all put strikes in the 0.10–0.40 delta range. Open interest is the total number of outstanding contracts — a measure of how actively traded the options chain is.',
    why: 'Thin chains mean wide spreads on entry and difficulty rolling if the trade moves against you. Liquid chains = trade near fair value, clean exits, and rolling to a new expiry without hunting for a counterparty.',
    formula: 'Filters candidates to 0.1 < |delta| < 0.4 first,\n  then takes median OI across those strikes.\n  pts = min(log10(OI) / log10(5000), 1.0) × 8\n  Log scale gives partial credit for smaller-cap chains.' },
  { factor: 'DTE Sweet Spot',  weight: 7,   detail: '30–45 DTE = 7 · 21–30 or 45–60 = 4.2 · 14–21 or 60–75 = 2.1 · <14 or >75 = 0.',
    definition: 'A bonus for selecting expirations in the theta-acceleration sweet spot (30–45 days). Theta decay accelerates non-linearly as expiry approaches, peaking in the 30–45 DTE band for premium sellers.',
    why: 'Too short = excessive gamma risk, ATM moves swing P&L violently. Too long = theta crawls, capital tied up unproductively. 30–45 DTE balances rate of decay against gamma exposure.',
    formula: 'Tiered by DTE bucket:\n  30 ≤ DTE ≤ 45 → 7 (sweet spot)\n  21–30 or 45–60 → 4.2 (acceptable)\n  14–21 or 60–75 → 2.1 (suboptimal)\n  else → 0' },
  { factor: 'Earnings in DTE', weight: -15, detail: 'Hard penalty if earnings fall within the expiry window.',
    definition: 'A binary flag — true if the company\'s next earnings announcement date falls within the option\'s expiration window (between today and the expiry date).',
    why: 'Earnings create overnight gap risk that can blow through your strike regardless of technicals. This is the most common cause of unexpected assignment on otherwise sound CSP setups — avoid unless intentional.',
    formula: 'earnings_within_dte = True if:\n  0 ≤ (earnings_date − today).days ≤ DTE\n  Source: yfinance calendarEvents.earnings' },
  { factor: '— STRIKE SCORE (×0.6) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'Delta',            weight: 15,  detail: '−0.20→−0.25=15 · ±1 band=10 · −0.10→−0.15=5 · <−0.30=5.8.',
    definition: 'The rate of change of the option\'s price per $1 move in the stock. For puts, delta ranges from 0 to −1. The absolute value approximates the market-implied probability the put expires in-the-money.',
    why: 'Delta approximates the probability of expiring in-the-money. −0.20 to −0.25 ≈ 20–25% ITM probability — the sweet spot for premium vs. risk. Closer = more premium but higher assignment odds; further = safer but premium too thin to justify tying up capital.',
    formula: 'Black-Scholes put delta:\n  d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\n  delta = N(d1) − 1\n  Rescaled from 18 → 15 (×15/18 throughout)' },
  { factor: 'Dist vs Support', weight: 18,  detail: '≤5% below strike→18–10 · 5–10% below→10–0 · >10% below=0 · all support above strike=7.',
    definition: 'The gap between the put strike and the nearest high-volume price level below the strike. Volume-profile support is a price zone where heavy buying has historically occurred, creating a natural demand floor.',
    why: 'A 6M volume-profile support level below your strike attracts buyers on a pullback, acting as a floor that limits how far price can fall through your strike. If ALL support levels are above your strike, the stock has been trending strongly upward (+7 pts).',
    formula: 'Volume Profile support (6M / 126-day lookback):\n  typical_price = (High + Low + Close) / 3\n  Bins 126d into 50 equal-width buckets; sums volume per bucket\n  Takes top-3 bins below current price; uses nearest below strike\n  Bonus: no support below strike but support data exists → +7' },
  { factor: 'Exp Move Buffer', weight: 20,  detail: '≥0.2σ outside=20 · 0–0.2σ→13 · −0.1–0σ→5 · deeper inside=0.',
    definition: 'How far outside the options-implied 1-standard-deviation expected move the strike sits, measured in units of that expected move. Positive = strike is beyond the statistical floor; negative = inside it.',
    why: 'Selling outside the 1σ expected move gives a >68% theoretical probability the stock stays above your strike. Every 0.1σ of additional buffer directly improves the edge built into options pricing at that strike.',
    formula: 'Expected move (1σ range):\n  EM = S × σ × √T    where T = DTE/365\n  EM_lower = S − EM\n  sigmas_outside = (EM_lower − strike) / EM\n  Positive = strike is outside the 1σ floor' },
  { factor: '% OTM from Spot', weight: 9,   detail: '≥15%=9 · ≥10%→6.75 · ≥5%→4.5 · ≥2%→1.5 · <2%=0.',
    definition: 'The raw percentage gap between current stock price and the strike. For a put, this is how far the stock must fall before the option goes in-the-money and assignment risk begins.',
    why: 'Raw price cushion independent of IV or time. More distance before going in-the-money is a concrete margin of safety regardless of what volatility is doing. Complements EM Buffer, which is volatility-adjusted.',
    formula: 'otm_pct = (S − K) / S × 100\n  Raw distance cushion (data-independent, robust to stale IV)\n  Rescaled from 12 → 9 (×0.75 throughout)' },
  { factor: 'Bid-Ask Spread',  weight: 23,  detail: '≤1%=23 · ≤3%→15.3 · ≤5%→8.5 · ≤8%→2.1 · >8%=0.',
    definition: 'The percentage difference between the ask and bid prices relative to the option midpoint: (ask − bid) / mid × 100. Lower means a tighter market and cheaper execution.',
    why: 'Wide spreads directly erode realized premium. A 10% spread on a $1.00 put loses $0.05–$0.10 on entry alone, and you pay it again on every roll. Execution quality determines what you actually collect vs. what the screen shows.',
    formula: 'spread_pct = (ask − bid) / mid × 100\n  where mid = (bid + ask) / 2\n  Rescaled from 27 → 23 (×23/27 throughout)' },
  { factor: 'OI / Volume',      weight: 5,   detail: 'Circuit-breaker · ≥1000=5 · ≥500→3.5 · ≥200→2 · ≥100→0 · <100=0.',
    definition: 'Open interest (when market closed) or today\'s volume (when market open) at this specific strike — a direct count of active participants.',
    why: 'High OI/volume at this specific strike = efficient price discovery, fast fills near mid, and a liquid exit if the stock moves against you. Low OI = you may be the only participant, making rolling or closing costly.',
    formula: 'Uses volume if US market is open (9:30–16:00 ET weekday)\n  Otherwise uses openInterest at this specific strike\n  Source: yfinance options chain row for the strike' },
  { factor: 'Annualized ROC',   weight: 10,  detail: '≥30%=10 · 20–30%→7→10 · 12–20%→4→7 · 6–12%→1→4 · <6%=0.',
    definition: 'Annualized return on capital required for a cash-secured put. Measures how much premium you actually collect relative to the cash tied up, normalized to a one-year timeframe.',
    why: 'The screener was previously scoring everything about safety and execution but nothing about how much you get paid. Two strikes with identical Δ/EM/spread can have wildly different yields; ROC closes that gap.',
    formula: 'capital_per_share = strike − credit\n  ROC = (credit / capital_per_share) × (365 / DTE) × 100\n  Provisional curve — calibrate against real strikes during validation' },
]

const SCORE_TIERS = [
  { range: '≥ 75', label: 'Take it',       color: '#4ade80', desc: 'All signals aligned, rare',                  action: 'Take it, normal size' },
  { range: '65–74', label: 'Take it',       color: '#86efac', desc: 'Solid trade with minor weakness',           action: 'Take it, understand the weakness' },
  { range: '55–64', label: 'Directional',   color: '#facc15', desc: 'Mechanically fine, thesis-dependent',       action: 'Only if you have a directional view' },
  { range: '45–54', label: 'Usually skip',  color: '#fb923c', desc: 'Something structural is off',               action: 'Usually skip' },
  { range: '< 45',  label: 'Skip',          color: '#f87171', desc: 'Multiple red flags',                        action: 'Skip' },
]

const DECISION_STEPS = [
  { n: 1, q: 'Score ≥ 70?',                                       a: 'Trade it. Steps 2–4 are confirmation, not a gate.' },
  { n: 2, q: 'Would I own the shares at this strike?',            a: 'If no, stop. A CSP is a conditional buy order — only sell it at a price you actually want to own.' },
  { n: 3, q: 'What are the 2 biggest factor drags?',              a: 'Lowest-scoring factors in Env and Strike define the “ticker question” — the specific risk this trade is paying you to accept.' },
  { n: 4, q: 'Can I articulate the thesis that overrides those drags?', a: 'If no, skip. If yes, size normally and write the thesis down before entering.' },
]

interface ExitNode { cond: string; action: string; tone?: 'close' | 'hold' | 'monitor' | 'assign' | 'roll' }
interface ExitBranch { label: string; children: ExitNode[] }
const EXIT_STRATEGY: ExitBranch[] = [
  {
    label: 'Position has ≥ 21 DTE',
    children: [
      { cond: 'Captured ≥ 50% premium',                       action: 'CLOSE',                     tone: 'close' },
      { cond: 'Captured ≥ 25% and > 21 DTE',                   action: 'Consider CLOSE (optional)', tone: 'close' },
      { cond: 'ITM (price < strike)',                          action: 'Monitor — no action yet',   tone: 'monitor' },
      { cond: 'OTM, < 25% captured',                            action: 'HOLD',                      tone: 'hold' },
    ],
  },
  {
    label: 'Position has < 21 DTE',
    children: [
      { cond: 'Captured ≥ 50%',                                 action: 'CLOSE',                     tone: 'close' },
      { cond: 'OTM (price > strike)',                                 action: 'Let it expire worthless — keep full premium', tone: 'hold' },
      { cond: 'ITM, still want to own shares at this strike',   action: 'Let assign',                tone: 'assign' },
      { cond: 'ITM, thesis broken or no longer want the stock', action: 'ROLL down/out for credit, else close for loss', tone: 'roll' },
    ],
  },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number, universe: UniverseKey) => void
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
  const [universe, setUniverse] = useState<UniverseKey>(DEFAULT_UNIVERSE)

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
    onScan(topN, scanMinDTE, scanMaxDTE, universe)
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
            <div className="score-tier-table-header">
              <span>Score</span>
              <span>Interpretation</span>
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
          <div className="decision-framework">
            <div className="decision-framework-header">Decision framework — run top-down per row</div>
            <ol className="decision-steps">
              {DECISION_STEPS.map(s => (
                <li key={s.n} className="decision-step">
                  <span className="decision-step-num">{s.n}</span>
                  <span className="decision-step-q">{s.q}</span>
                  <span className="decision-step-a">{s.a}</span>
                </li>
              ))}
            </ol>
          </div>
          <div className="exit-strategy">
            <div className="decision-framework-header">Exit strategy — manage after fill</div>
            {EXIT_STRATEGY.map(branch => (
              <div key={branch.label} className="exit-branch">
                <div className="exit-branch-label">{branch.label}</div>
                <ul className="exit-children">
                  {branch.children.map(n => (
                    <li key={n.cond} className="exit-child">
                      <span className="exit-cond">{n.cond}</span>
                      <span className="exit-arrow">→</span>
                      <span className={`exit-action exit-action-${n.tone ?? 'hold'}`}>{n.action}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <div className="thumb-rule">
              <span className="thumb-rule-label">Thumb rule</span>
              <span className="thumb-rule-text">
                At 21 DTE: <em>is remaining premium worth the gamma risk?</em>
                &nbsp;Close if near-the-money or you don’t want assignment. Run it only if deep OTM with thin extrinsic, or you want the shares at this strike.
              </span>
            </div>
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
                        style={{ color: f.weight < 0 ? '#f87171' : f.weight >= 20 ? '#4ade80' : f.weight >= 10 ? '#fbbf24' : '#94a3b8' }}
                      >
                        {f.weight > 0 ? `+${f.weight}` : f.weight} pts
                      </span>
                      <div className="score-factor-bar-wrap">
                        <div className="score-factor-bar" style={{
                          width: f.weight <= 0 ? '0%' : `${Math.min(Math.abs(f.weight) / 30 * 100, 100)}%`,
                          background: f.weight >= 20 ? '#4ade80' : f.weight >= 10 ? '#fbbf24' : '#94a3b8'
                        }} />
                      </div>
                      <span className="score-factor-detail">{f.detail}</span>
                    </div>
                    {expandedFactor === f.factor && (f.definition || f.why || f.formula) && (
                      <div className="score-factor-expanded">
                        {f.definition && <p className="score-factor-definition"><strong>What</strong>{f.definition}</p>}
                        {f.why && <p className="score-factor-why"><strong>Why</strong>{f.why}</p>}
                        {f.formula && <pre className="score-factor-formula">{f.formula}</pre>}
                      </div>
                    )}
                  </div>
            ))}
            <div style={{ marginTop: '6px', padding: '5px 8px', background: '#0f172a', borderRadius: '5px', fontSize: '11px', color: '#64748b', borderLeft: '3px solid #334155' }}>
              <strong style={{ color: '#94a3b8' }}>Tie-break:</strong> equal scores → higher <strong>Ann. ROC</strong> wins.
            </div>
          </div>
        </div>
      )}

      {mode === 'scan' && (
        <div className="momentum-scan-row">
          <div className="momentum-scan-info">
            <span className="scan-desc">
              Scans <strong>{universeSize(universe)}</strong> stocks — {UNIVERSE_OPTIONS.find(o => o.key === universe)?.hint}
            </span>
            <span className="app-subtitle">Ranked by CSP composite score — returns top candidates automatically</span>
          </div>
          <div className="momentum-scan-controls">
            <label className="filter-item">
              Universe
              <select
                className="filter-select"
                value={universe}
                onChange={e => setUniverse(e.target.value as UniverseKey)}
                disabled={loading}
              >
                {UNIVERSE_OPTIONS.map(o => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
            </label>
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

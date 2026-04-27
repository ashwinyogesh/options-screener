import { useState, useRef, KeyboardEvent } from 'react'
import { UNIVERSE_OPTIONS, DEFAULT_UNIVERSE, universeSize, type UniverseKey } from '../constants/universes'

const PRESET_BASKET = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'AMD']

// ---------------------------------------------------------------------------
// Score legend data
// ---------------------------------------------------------------------------

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.5) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  {
    factor: 'Trend Strength', weight: 30, detail: 'P>SMA50>SMA200=30 ✓ · P>SMA50 only=18 ✗ · SMA50>SMA200 only=10 ✗ · above SMA200=5 ✗ · else=0 ✗  (✗ = hard gate fires)',
    definition: 'Whether price is above SMA50 and SMA50 is above SMA200 — full three-level alignment is required. Only 30 pts clears the hard gate (threshold = 22). All lower tiers are computed for transparency but trigger ENV = 0.',
    why: 'DITM long calls are pure directional bets. The strongest trend (all three in sequence) minimises the risk the stock reverses through your strike before expiry. Partial alignment (e.g. price above SMA50 but SMA50 below SMA200) is not sufficient — the broader trend is not confirmed.',
    formula: 'SMA50 = rolling mean of Close over last 50 days\nSMA200 = rolling mean of Close over last 200 days\nP>SMA50>SMA200 → 30 pts  ← only tier that clears the hard gate (≥ 22)\nP>SMA50 only   → 18 pts  ← hard gate fires (18 < 22) → ENV = 0\nSMA50>SMA200   → 10 pts  ← hard gate fires\nAbove SMA200   →  5 pts  ← hard gate fires\nElse           →  0 pts  ← hard gate fires',
  },
  {
    factor: 'HV Rank (inv.)', weight: 12, detail: '≤20=12 · 20–40→8–12 · 40–60→4–8 · 60–80→1–4 · >80=0.',
    definition: 'Inverted HV Rank: low HV rank means options are historically cheap — better for buyers.',
    why: "Buying DITM calls when vol is elevated means paying up for time value you don't need. Low HV rank = cheap extrinsic on the option, which is what we want.",
    formula: 'hv_rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100\nInverted: ≤20=12 (cheapest), ≥80=0 (most expensive)',
  },
  {
    factor: 'Weekly RSI(14)', weight: 10, detail: '50–65=10 · 45–50 or 65–70→7 · 40–45 or 70–75→4 · 35–40+strong trend→6 · else=0.',
    definition: 'RSI computed on weekly closes (resample daily to weekly, Wilder smoothing). Measures medium-term momentum — a cleaner signal for multi-month DITM positions.',
    why: 'Weekly momentum in the sweet spot (50–65) confirms sustained uptrend without being overextended. A weekly RSI in oversold territory (35–40) plus a confirmed trend can signal a strong pullback-entry point.',
    formula: 'Resample daily Close to weekly (last close of each week)\nWilder RSI(14) on weekly series\nScore: 50–65=10, 45–50 or 65–70=7, 40–45 or 70–75=4, 35–40+Trend≥22=6, else=0',
  },
  {
    factor: '52W High Dist.', weight: 12, detail: '3–10%=12 · 0–3%=7 · 10–20%=9 · 20–30%=4 · >30%=0.',
    definition: 'How far below the 52-week high the stock is currently trading.',
    why: 'DITM calls need a trend. 3–10% below the high is the sweet spot: the stock has momentum and is pulling back slightly, not overextended at the exact top. Stocks >30% below their high are in a downtrend.',
    formula: 'dist = abs(min((Close − max_252d_close) / max_252d_close × 100, 0))\nTiered: 3–10%=12 · 0–3%=7 · 10–20%=9 · 20–30%=4 · >30%=0',
  },
  {
    factor: '200d Return', weight: 15, detail: '≥25%=15 · 15–25%→11–15 · 5–15%→6–11 · 0–5%→1–6 · <0%=0.',
    definition: 'How much the stock has appreciated vs. its price approximately 200 days ago (median-anchored to smooth noise). Measures sustained long-term momentum.',
    why: 'A +15–25% gain over 200 days is the ideal DITM environment: confirmed uptrend with room left. Negative returns mean you are trying to catch a falling knife — avoid.',
    formula: 'anchor = median(Close[-205:-200])  (5-day median ~200d ago)\nret_200d = Close_today / anchor − 1',
  },
  {
    factor: 'Days to Earnings', weight: 8, detail: '≤7=0 (gate) · 8–14=3 · 15–60=8 · >60 or none=8.',
    definition: 'Calendar days until the next earnings announcement. Earnings create overnight gap risk that can rapidly change the directional thesis.',
    why: 'Earnings within a week (≤7d) is a hard gate: IV spike + gap risk makes pricing unreliable. 8–14d is penalised. Further out = full credit.',
    formula: 'days = (earnings_date − today).days\nHard gate: ≤7 → env_score = 0',
  },
  {
    factor: 'Chain Liquidity', weight: 13, detail: 'log₁₀(median_OI)/log₁₀(500) × 13 · capped at 13.',
    definition: 'Median open interest across the 0.60–0.95 delta range of the call chain. DITM options are typically less liquid than ATM — reference is 500 OI (vs 5000 for CSP/CC).',
    why: 'Illiquid DITM chains = wide spreads on entry, and difficulty exiting or rolling. Even moderate OI (500+) is sufficient for liquid fills on DITM calls.',
    formula: 'pts = min(log10(median_OI) / log10(500), 1.0) × 13\nMedian of OI across 0.60–0.95 delta call strikes',
  },
  { factor: '— STRIKE SCORE (×0.5) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  {
    factor: 'Delta', weight: 22, detail: '0.80–0.85=22 · 0.75–0.80 or 0.85–0.90→18 · 0.70–0.75 or >0.90→13 · <0.70=0.',
    definition: 'Black-Scholes call delta. For DITM calls, delta is high (0.70–0.95+), meaning the option moves nearly dollar-for-dollar with the stock.',
    why: "Sweet spot 0.80–0.85: you get most of the upside (80–85¢ per $1 move) while paying less extrinsic than an ATM call. Below 0.70 = not DITM enough; above 0.90 = excessive cost for marginal improvement.",
    formula: 'Black-Scholes call delta:\n  d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\n  delta = N(d1)',
  },
  {
    factor: 'Extrinsic %', weight: 28, detail: '<2%=28 · 2–4%→22–28 · 4–6%→16–22 · 6–9%→7–16 · 9–12%→0–7 · >12%=0.',
    definition: 'Extrinsic value (time value) as a percentage of strike price. Extrinsic = mid − intrinsic, where intrinsic = max(price − strike, 0).',
    why: 'The entire premise of DITM: minimise the extrinsic you pay. Extrinsic is money that evaporates to theta. <2% of strike means almost all your premium is pure intrinsic — you are essentially buying stock on leverage with bounded downside.',
    formula: 'intrinsic = max(price − strike, 0)\nextrinsic = mid − intrinsic\nextrinsic_pct = extrinsic / strike × 100',
  },
  {
    factor: 'Annualised Theta %', weight: 17, detail: '<5%=17 · 5–10%→12–17 · 10–15%→7–12 · 15–20%→2–7 · >20%=0.',
    definition: 'Annualised Black-Scholes theta expressed as a percentage of the strike price. Measures how much of the strike you pay per year just to hold the option.',
    why: 'Low theta % = cheap carry cost. This directly penalises excessively expensive options relative to the strike. A DITM call with >20% annualised theta is burning money faster than a stock loan.',
    formula: 'theta_annual = BS theta (per year, negative for longs)\ntheta_ann_pct = |theta_annual| / strike × 100',
  },
  {
    factor: 'IV Percentile', weight: 10, detail: '≤25=10 · 25–50→7–10 · 50–75→3–7 · >75=0.',
    definition: 'HV-based IV percentile: % of days in the past year when IV was lower than today. Lower = buying when options are cheap.',
    why: 'Unlike CSP/CC (premium sellers who want high IV), DITM buyers want low IV. You want to buy a DITM call when options are historically cheap, not when the market is pricing in big moves.',
    formula: 'iv_percentile = % of last 252d where HV < today HV\nScored inversely: ≤25th pct = full marks',
  },
  {
    factor: 'Bid-Ask Spread', weight: 18, detail: '≤2%=18 · 2–4%→13–18 · 4–7%→7–13 · 7–12%→1–7 · >12%=0.',
    definition: '(Ask − Bid) / Mid × 100. The transaction cost paid on entry — and again on exit.',
    why: 'Wide spreads are especially costly for DITM calls because you pay them on large-notional positions. A 10% spread on a $80 DITM call costs $8 on entry alone ($16 round-trip per contract).',
    formula: 'spread_pct = (ask − bid) / mid × 100\nwhere mid = (bid + ask) / 2',
  },
  {
    factor: 'Capital Efficiency', weight: 5, detail: '25–35%=5 · 35–50%→3–5 · 50–65%→1–3 · >65%=0.',
    definition: 'Option mid price as a % of underlying price. Measures how much capital you deploy relative to just buying the stock.',
    why: 'DITM calls should cost 25–35% of the stock price to get delta ~0.80–0.85. Lower = strike too far from money (not DITM). Higher = strike close to ATM, paying too much time value.',
    formula: 'capital_efficiency_pct = mid / price × 100',
  },
]

const HARD_GATES = [
  { gate: 'Trend < 22 pts',     effect: 'ENV = 0', reason: 'Effectively: not P>SMA50>SMA200. The threshold (22) sits between 18 pts (P>SMA50 only) and 30 pts (full alignment) — only full alignment passes. Buying a DITM call without a confirmed uptrend is directionally wrong.' },
  { gate: 'HV Rank > 50',       effect: 'ENV = 0', reason: 'Options are priced above their historical median — you are paying above-fair-value extrinsic, defeating the core premise of DITM.' },
  { gate: 'Earnings ≤ 7 days',  effect: 'ENV = 0', reason: 'IV spike + overnight gap risk make all option pricing unreliable. Wait until after the print.' },
]

const SCORE_TIERS = [
  { range: '≥ 80', label: 'Strong',   color: '#4ade80', desc: 'Strong trend + cheap extrinsic',        action: 'Take it, normal size' },
  { range: '65–79', label: 'Solid',    color: '#86efac', desc: 'Solid setup, understand the drag',      action: 'Take it, understand the weakness' },
  { range: '50–64', label: 'Moderate', color: '#facc15', desc: 'Trend confirmed, one factor weak',      action: 'Only with strong conviction' },
  { range: '35–49', label: 'Weak',     color: '#fb923c', desc: 'Multiple factor drags',                 action: 'Usually skip' },
  { range: '< 35',  label: 'Avoid',   color: '#f87171', desc: 'Hard gate triggered or scattered score', action: 'Skip' },
]

const DECISION_STEPS = [
  { n: 1, q: 'Score ≥ 65?',                                                   a: 'Trade it. Steps 2–4 are confirmation, not a gate.' },
  { n: 2, q: 'Is trend confirmed? (P > SMA50 > SMA200)',                       a: 'If no, stop. This is a hard gate — without a full uptrend, DITM calls are trend-fighting trades.' },
  { n: 3, q: 'What are the 2 biggest factor drags? (ENV + Strike breakdown)',  a: 'The lowest-scoring factors define the specific risk being priced in. Name them before entering.' },
  { n: 4, q: 'Can I define the thesis: duration, target, and catalyst?',       a: 'If no, skip. DITM calls require a specific view — not just "bullish". Write down: entry, exit target, max loss date.' },
]

interface ExitNode { cond: string; action: string; tone?: 'close' | 'hold' | 'monitor' | 'assign' | 'roll' }
interface ExitBranch { label: string; children: ExitNode[] }
const EXIT_STRATEGY: ExitBranch[] = [
  {
    label: 'Profit-side (scale out)',
    children: [
      { cond: '+25% on option mid',                                     action: 'Consider partial close (25% of position)',       tone: 'hold' },
      { cond: '+50%',                                                   action: 'Close 50% — lock in most of the gain',           tone: 'close' },
      { cond: '+100%',                                                  action: 'Close remainder or trail the rest',              tone: 'close' },
      { cond: '+150%+',                                                 action: 'Let the final tranche run with a stop',          tone: 'close' },
    ],
  },
  {
    label: 'Loss-side (defence)',
    children: [
      { cond: '−35% on option mid',                                     action: 'Hard stop — exit immediately',                  tone: 'roll' },
      { cond: 'Price breaks SMA200',                                    action: 'Exit — trend thesis is invalidated',             tone: 'roll' },
      { cond: 'Score drops below 35',                                   action: 'Re-evaluate; close if no recovery thesis',      tone: 'monitor' },
      { cond: '120 DTE checkpoint',                                     action: 'Review: roll forward if score ≥ 50; close if not', tone: 'monitor' },
    ],
  },
  {
    label: 'Roll triggers',
    children: [
      { cond: 'Score < 50 + ≥ 90 DTE remaining + trend intact',        action: 'Roll to next cycle for credit or small debit',  tone: 'roll' },
      { cond: 'Score ≥ 50 + < 60 DTE remaining',                       action: 'Hold — still has time value worth keeping',     tone: 'hold' },
    ],
  },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number, universe: UniverseKey) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
}

export function DitmInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')
  const [showLegend, setShowLegend] = useState(false)
  const [expandedFactor, setExpandedFactor] = useState<string | null>(null)

  // Scan mode
  const [topN, setTopN] = useState(20)
  const [scanMinDTE, setScanMinDTE] = useState(180)
  const [scanMaxDTE, setScanMaxDTE] = useState(365)
  const [universe, setUniverse] = useState<UniverseKey>(DEFAULT_UNIVERSE)

  // Custom mode
  const [chips, setChips] = useState<string[]>([])
  const [inputValue, setInputValue] = useState('')
  const [minDTE, setMinDTE] = useState(180)
  const [maxDTE, setMaxDTE] = useState(365)
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
    if (minDTE > maxDTE) err = 'Min DTE must be ≤ Max DTE'
    else if (minDTE < 30 || maxDTE > 730) err = 'DTE must be between 30 and 730'
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
          title="How the DITM score is calculated"
        >
          {showLegend ? '▲ Score Guide' : '▼ Score Guide'}
        </button>
      </div>

      {showLegend && (
        <div className="score-legend">
          {/* Score tiers */}
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

          {/* Decision framework */}
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

          {/* Exit strategy */}
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
                At 120 DTE: <em>is the trend still intact and extrinsic still cheap?</em>
                &nbsp;Roll forward if score ≥ 50 and trend holds. Exit if thesis is broken.
              </span>
            </div>
          </div>

          {/* Hard gates */}
          <div className="score-legend-factors">
            <div className="score-legend-header">Hard gates — any of these forces ENV = 0 (final ≤ ~50)</div>
            {HARD_GATES.map(g => (
              <div key={g.gate} className="score-factor-block">
                <div className="score-factor-row">
                  <span className="score-factor-name" style={{ color: '#f87171' }}>{g.gate}</span>
                  <span className="score-factor-weight" style={{ color: '#f87171' }}>{g.effect}</span>
                  <span className="score-factor-detail">{g.reason}</span>
                </div>
              </div>
            ))}
          </div>

          {/* Score breakdown */}
          <div className="score-legend-factors">
            <div className="score-legend-header">Score breakdown — Final = 0.5 × Env + 0.5 × Strike</div>
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
                        style={{ color: f.weight >= 20 ? '#4ade80' : f.weight >= 10 ? '#fbbf24' : '#94a3b8' }}
                      >
                        +{f.weight} pts
                      </span>
                      <div className="score-factor-bar-wrap">
                        <div className="score-factor-bar" style={{
                          width: `${Math.min(f.weight / 30 * 100, 100)}%`,
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
              <strong style={{ color: '#94a3b8' }}>Tie-break:</strong> equal scores → closest to <strong>0.82 delta</strong> wins. If also equal → lower <strong>Extrinsic%</strong> wins.
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
            <span className="app-subtitle">Ranked by DITM composite score — returns top DITM long call candidates automatically</span>
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
                min={30}
                max={730}
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
                min={30}
                max={730}
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
                  min={30}
                  max={730}
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
                  min={30}
                  max={730}
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

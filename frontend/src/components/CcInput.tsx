import { useState, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { UNIVERSE_OPTIONS, DEFAULT_UNIVERSE, universeSize, type UniverseKey } from '../constants/universes'

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.4) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'IV Percentile',   weight: 35,  detail: '<30th=0 · 30–50th→0→10 · 50–75th→10→25 · 75–90th→25→35 · ≥90th=35. HV-derived — never stale.',
    definition: '% of the last 252 trading days where the 30-day Historical Volatility was lower than today\u2019s. Measures whether options are elevated relative to this stock\u2019s own recent history.',
    why: 'Regime-agnostic: a stable stock (AAPL, KO) during its own elevated-IV period scores well even if its absolute IV/HV ratio is near 1.0. The old IV/HV ratio structurally favoured high-beta names because their elevated realized vol kept the ratio low during calm periods. IV percentile removes that bias.',
    formula: 'hv = rolling_30d_std(log_returns) \u00d7 sqrt(252)\n  iv_percentile = % of last-252d where hv[t] < hv[today]\n  v3.3: replaces IV/HV ratio (35 pts). Curve: <30th=0, 30\u201350th\u21920\u219210, 50\u201375th\u219210\u219225, 75\u201390th\u219225\u219235, \u226590th=35' },
  { factor: 'Trend: 52W Dist',  weight: 15,  detail: 'CC tent: ≤5%=0 · 5–15%→0→15 · 15–35%→15→0 · >35%=0.',
    definition: 'Direction-aware trend factor based on distance from the 52-week high. v3.1 rescaled from 25 to 15 pts — 10 pts moved to SMA Alignment + SMA Slope sub-factors.',
    why: 'For CCs: stock at the 52W high has the most upside momentum and highest risk of being called away. Modest consolidation 5–15% below the high gives room to drift sideways while premium decays. v3.1 narrows the tent range (sweet spot peaks at 10% consolidation, zero at 35%).',
    formula: 'dist = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100\n  pct_below = abs(min(dist, 0))\n  v3.1: 15 pts max, tent: ≤5%=0, ramp 5→15% (0→15), decay 15→35% (15→0)' },
  { factor: 'Trend: SMA Align', weight: 5,   detail: 'sma_ratio >1.02 = 5 · 1.0–1.02 = 3 · 0.98–1.0 = 1.5 · <0.98 = 0.',
    definition: 'SMA50/SMA200 ratio — captures whether the 50-day average is above the 200-day average. Restored in v3.1 as an independent 5-pt sub-factor separate from 52W proximity.',
    why: 'SMA alignment is structurally different from 52W proximity. For CCs, rising SMA50 above SMA200 confirms bullish structure; used with trend context since you want the underlying to remain range-bound, not break out.',
    formula: 'sma_ratio = SMA50 / SMA200\n  >1.02 = full credit; 1.0–1.02 = partial; 0.98–1.0 = minimal; <0.98 = 0' },
  { factor: 'Trend: SMA Slope', weight: 5,   detail: '≥0.5%=5 · 0.2–0.5% linear 3→5 · 0–0.2% linear 0→3 · negative=0.',
    definition: 'SMA50 10-day percentage change — momentum confirmation. Measures whether the 50-day moving average is rising or falling over the last two weeks of trading.',
    why: 'Rising SMA50 confirms structural trend health. For CCs, a flat or slightly rising slope is ideal — too steep means risk of a breakout through your strike.',
    formula: 'sma50_slope_pct = (SMA50[−1] / SMA50[−11] − 1) × 100\n  10 trading-day window; 0.0 default if insufficient history' },
  { factor: 'RSI(14)',          weight: 20,  detail: 'CC: 38–58=20 · 30–38 linear 0→20 · 58–75 linear 20→0 · <30 or >75=0. Ceiling extended to 75 for AAPL/MSFT-style names in normal trends.',
    definition: 'Relative Strength Index: a momentum oscillator (0–100) measuring the magnitude of recent gains vs. losses over the last 14 trading sessions. Above 70 = overbought; below 30 = oversold.',
    why: 'For CCs: mild weakness (RSI 38–58) favors call sellers — momentum has cooled and the stock is unlikely to surge through your strike. v3 audit fix #8: extended ceiling from 70 to 75 because the v2 knife-edge sent NVDA-style RSI 72 names to 0; now decay is smoother.',
    formula: 'Wilder-smoothed RSI(14)\n  v3 rescale: 10 → 20 pts\n  Audit fix #8: ceiling extended 70 → 75; smooth ramp 58–75' },
  { factor: 'Chain Median OI', weight: 20,  detail: 'Circuit-breaker · log₁₀(OI)/log₁₀(5000) × 20 · saturates near 20 for any liquid name; gives small-caps partial credit on log scale.',
    definition: 'The median open interest across all call strikes in the 0.10–0.40 delta range. Open interest is the total number of outstanding contracts — a measure of how actively traded the options chain is.',
    why: 'Thin chains mean wide spreads on entry and difficulty rolling if the stock moves against you. Liquid chains = trade near fair value, clean exits, and rolling to a later expiry without hunting for a counterparty.',
    formula: 'pts = min(log10(chain_median_oi) / log10(5000), 1.0) × 20\n  v3 rescale: 8 → 20 pts (was a circuit-breaker, now a meaningful liquidity floor)' },
  { factor: 'Earnings in DTE', weight: -15, detail: 'Hard penalty if earnings fall within the expiry window.',
    definition: 'A binary flag — true if the company\'s next earnings announcement date falls within the option\'s expiration window (between today and the expiry date).',
    why: 'Earnings create gap risk in both directions. A post-earnings surge can call your shares away; a collapse damages your underlying. Avoid unless you specifically want to sell a call ahead of earnings.',
    formula: 'earnings_within_dte = True if:\n  0 ≤ (earnings_date − today).days ≤ DTE' },
  { factor: '— STRIKE SCORE (×0.6) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'Delta',            weight: 25,  detail: 'Smooth bell · |Δ−(+0.225)| ≤ 0.025 = 25 · 0.025–0.075→25→16 · 0.075–0.125→16→9 · 0.125–0.175→9→0 · >0.175=0.',
    definition: 'The rate of change of the option\'s price per $1 move in the stock. For calls, delta ranges from 0 to +1. It approximates the market-implied probability the call expires in-the-money (stock gets called away).',
    why: 'Call delta approximates the probability of expiring in-the-money. +0.225 is the sweet spot for premium vs. keeping shares. v3.1 raised from 20 to 25 pts and replaced step-bands with smooth piecewise-linear decay.',
    formula: 'Black-Scholes call delta:\n  d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\n  call_delta = N(d1)\n  v3.1: smooth bell max=25, ideal = +0.225, piecewise-linear bands' },
  { factor: 'Bid-Ask Spread',  weight: 25,  detail: '≤1%=25 · 1–3%→25→17 · 3–5%→17→9 · 5–8%→9→2 · >8%=0.',
    definition: 'The percentage difference between the ask and bid prices relative to the option midpoint: (ask − bid) / mid × 100. Lower means a tighter market and cheaper execution.',
    why: 'Wide spreads directly erode realized premium. A 10% spread on a $1.00 call loses $0.05–$0.10 on entry alone, and you pay it again on every roll. v3.1 lowered from 30 to 25 pts — rebalanced with Delta which was raised to 25.',
    formula: 'spread_pct = (ask − bid) / mid × 100\n  v3.1: max 25 pts (was 30); rebalanced vs Delta' },
  { factor: 'OI / Volume',      weight: 15,  detail: 'Circuit-breaker · ≥1000=15 · 500–1000→10.5→15 · 200–500→6→10.5 · 100–200→0→6 · <100=0.',
    definition: 'Open interest (when market closed) or today\'s volume (when market open) at this specific strike — a direct count of active participants.',
    why: 'High OI/volume at this specific strike = efficient price discovery, fast fills near mid, and a liquid exit if the stock surges toward your strike. Low OI = you may be the only participant, making rolling or closing costly.',
    formula: 'Uses volume if US market is open (9:30–16:00 ET weekday)\n  Otherwise uses openInterest at this specific call strike\n  v3 rescale: 5 → 15 pts' },
  { factor: 'Annualized ROC',   weight: 35,  detail: '≥12%=35 · 8–12%→24.5→35 · 4–8%→14→24.5 · 2–4%→3.5→14 · 1–2%→0→3.5 · <1%=0.',
    definition: 'Annualized return on capital required to hold the underlying shares against a covered call. Measures premium yield against the cash value of the shares, normalized to a one-year timeframe.',
    why: 'ROC is the actual yield — the primary objective for a premium seller. v3.1 lowered the ceiling from 20% to 12% so stable low-IV names (KO, JNJ) reach full credit at realistic premium levels — removes vol-bias that structurally rewarded NVDA-class names.',
    formula: 'capital_per_share = current_price − credit\n  ROC = (credit / capital_per_share) × (365 / DTE) × 100\n  CC capital basis = current price (the underlying held to write the call)\n  v3.1: ceiling 20% → 12%; ≥12% = 35 pts full credit' },
  { factor: '— DIAGNOSTIC ONLY (not scored in v3) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'Exp Move Buffer', weight: 0,   detail: 'Computed and shown in the table for visibility. Contributes 0 to score in v3.',
    definition: 'How far above the 0.5× expected move boundary the strike sits, measured in units of the full expected move. Positive = strike is well above the reference ceiling.',
    why: 'Dropped from scoring in v3 (ADR-0007) — the factor was deterministically positive at the configured ideal delta, contributing redundant signal with Δ and %OTM.',
    formula: 'EM = S × σ × √T    where T = DTE/365\n  EM_half_upper = S + 0.5 × EM\n  sigmas_outside = (strike − EM_half_upper) / EM\n  Returned as em_buffer_pct in the response payload but not scored.' },
  { factor: '% OTM from Spot', weight: 0,   detail: 'Computed and shown in the table for visibility. Contributes 0 to score in v3.',
    definition: 'The raw percentage gap between the strike and current stock price.',
    why: 'Dropped from scoring in v3 — deterministic function of Δ and IV; redundant with Δ.',
    formula: 'otm_pct = (K − S) / S × 100\n  Returned in the response payload but not scored.' },
]

const SCORE_TIERS = [
  { range: '≥ 83', label: 'Excellent',     color: '#4ade80', desc: 'Top decile — best retention, lowest opportunity cost',  action: 'Take it, size up if thesis matches' },
  { range: '79–82', label: 'Strong',        color: '#86efac', desc: 'Phi crosses positive — upside preserved net of called-away cost', action: 'Take it, normal size' },
  { range: '72–78', label: 'Good',          color: '#bef264', desc: 'Clear lift over middle pack — solid trade',              action: 'Take it, understand the weakest factor' },
  { range: '56–71', label: 'Marginal',      color: '#facc15', desc: 'Noisy middle — score barely separates from random',     action: 'Only with a directional thesis' },
  { range: '< 56',  label: 'Skip',          color: '#f87171', desc: 'Worst retention (57%), highest opp cost, deeply negative phi', action: 'Skip' },
]

const DECISION_STEPS = [
  { n: 1, q: 'Score ≥ 72?',                                              a: 'Trade it. Below 72 the score barely separates from random. Steps 2–4 are confirmation, not a gate.' },
  { n: 2, q: 'Am I OK getting called away at this strike?',              a: 'If no, stop. A CC is a conditional sell — only sell the call at a price you’d actually take for the shares.' },
  { n: 3, q: 'What are the 2 biggest factor drags?',                     a: 'Lowest-scoring factors in Env and Strike define the “ticker question” — the specific risk this trade is paying you to accept.' },
  { n: 4, q: 'Can I articulate the thesis that overrides those drags?',  a: 'If no, skip. If yes, size normally and write the thesis down before entering.' },
]

interface ExitNode { cond: string; action: string; tone?: 'close' | 'hold' | 'monitor' | 'assign' | 'roll' }
interface ExitBranch { label: string; children: ExitNode[] }
const EXIT_STRATEGY: ExitBranch[] = [
  {
    label: 'Position has ≥ 21 DTE',
    children: [
      { cond: 'Captured ≥ 50% premium',                          action: 'CLOSE',                     tone: 'close' },
      { cond: 'Captured ≥ 25% and > 21 DTE',                      action: 'Consider CLOSE (optional)', tone: 'close' },
      { cond: 'ITM (price > strike)',                              action: 'Monitor — no action yet',   tone: 'monitor' },
      { cond: 'OTM, < 25% captured',                                action: 'HOLD',                      tone: 'hold' },
    ],
  },
  {
    label: 'Position has < 21 DTE',
    children: [
      { cond: 'Captured ≥ 50%',                                    action: 'CLOSE',                     tone: 'close' },
      { cond: 'OTM (price < strike)',                                 action: 'Let it expire worthless — keep full premium + shares', tone: 'hold' },
      { cond: 'ITM, strike ≥ cost basis, happy to sell here',       action: 'Let assign',                tone: 'assign' },
      { cond: 'ITM, thesis broken or strike below cost basis',      action: 'ROLL up/out for credit, else accept the called-away loss', tone: 'roll' },
    ],
  },
  {
    label: 'Roll mechanics — when ROLL is the action above',
    children: [
      { cond: 'Trigger — act when ANY fires',  action: 'Δ ≥ +0.40 · spot within 2% above strike · ≥ 50% premium captured with > 21 DTE · ≤ 21 DTE with Δ ≥ +0.30 · thesis break (earnings pre-announce, breakout above resistance)', tone: 'monitor' },
      { cond: 'Target — up-and-out, net credit', action: 'Next monthly expiry · new strike near +0.225 Δ at current spot · must be net credit · never roll to strike below cost basis · chain BA ≤ 5% & OI ≥ 200', tone: 'roll' },
      { cond: 'Stop — stop rolling when ANY fires', action: 'Roll target > 15% above original strike · 3 rolls deep · no net-credit roll available without going below cost basis → accept called-away or buy back at loss', tone: 'close' },
    ],
  },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number, universe: UniverseKey) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
}

export function CcInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('custom')
  const [showLegend, setShowLegend] = useState(false)
  const [expandedFactor, setExpandedFactor] = useState<string | null>(null)

  const [topN, setTopN] = useState(20)
  const [scanMinDTE, setScanMinDTE] = useState(30)
  const [scanMaxDTE, setScanMaxDTE] = useState(60)
  const [universe, setUniverse] = useState<UniverseKey>(DEFAULT_UNIVERSE)

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
          className={`mode-btn${mode === 'custom' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('custom')}
          disabled={loading}
        >
          Custom Symbols
        </button>
        <button
          className={`mode-btn${mode === 'scan' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('scan')}
          disabled={loading}
        >
          ⚡ Auto Scan
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
                &nbsp;Close if near-the-money or you don’t want to be called away. Run it only if deep OTM with thin extrinsic, or strike ≥ basis and you’re happy to sell here.
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
            <span className="app-subtitle">Ranked by CC composite score — returns top candidates automatically</span>
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

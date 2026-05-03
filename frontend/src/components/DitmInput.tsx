import { useState, useRef, KeyboardEvent } from 'react'
import { UNIVERSE_OPTIONS, DEFAULT_UNIVERSE, universeSize, type UniverseKey } from '../constants/universes'

const PRESET_BASKET = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'AMD']

// ---------------------------------------------------------------------------
// Score legend data
// ---------------------------------------------------------------------------

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.5) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  {
    factor: 'Trend Strength', weight: 25, detail: 'P>SMA50>SMA200=25 · P>SMA50 only=15 · SMA50>SMA200 only=8 · above SMA200=4 · else=0.',
    definition: 'Whether price is above SMA50 and SMA50 is above SMA200. v3: soft factor (no longer a hard gate). Full alignment is still the strongest tier; partial alignment earns proportional pts.',
    why: 'DITM long calls are pure directional bets. The strongest trend (P>SMA50>SMA200) confirms the broader uptrend. v2 used Trend < 22 pts as a hard gate that zeroed ENV — v3 keeps Trend as the highest-weighted ENV factor instead, so failing alignment costs ~17 pts but doesn\'t crater the whole score.',
    formula: 'P>SMA50>SMA200 → 25 pts\nP>SMA50 only   → 15 pts\nSMA50>SMA200   →  8 pts\nAbove SMA200   →  4 pts\nElse           →  0 pts',
  },
  {
    factor: '200d Return', weight: 15, detail: '≥25%=15 · 15–25%→11–15 · 5–15%→6–11 · 0–5%→1.5–6 · <0%=0.',
    definition: 'How much the stock has appreciated vs. its price approximately 200 days ago (median-anchored to smooth noise). Sustained long-term momentum. v3.2: weight compressed 25→15 to reduce momentum-cluster dominance; Trend Stability R² added as the orthogonal replacement signal.',
    why: 'A +15–25% gain over 200 days confirms a strong trend. Weight reduced so a Trend Stability signal (R²) can add an independent dimension — direction/magnitude was already captured by Trend Strength (25 pts), so awarding 25 pts here was doubling that signal.',
    formula: 'anchor   = median(Close[-205:-200])\nret_200d = Close_today / anchor − 1',
  },
  {
    factor: 'Trend Stability (R²)', weight: 10, detail: '≥0.85=10 · 0.70–0.85→7.5–10 · 0.50–0.70→4–7.5 · 0.30–0.50→1–4 · <0.30=0.',
    definition: 'R² of a 50-day OLS linear regression of closing prices. Measures how smooth and consistent the trend is — orthogonal to direction (Trend Strength) and magnitude (200d Return).',
    why: 'DITM delta-heavy positions bleed theta slowly but bleed it steadily in choppy, range-bound markets. A clean R² ≥ 0.70 means the trend has been a sustained drift rather than noise. Low R² = high chop = expensive to hold. This is the key de-correlation fix from the v3.2 audit.',
    formula: '_x      = [0, 1, …, n-1]  (last 50 days)\ncoeffs = np.polyfit(_x, Close, 1)\nfitted = np.polyval(coeffs, _x)\nR² = 1 − SS_res / SS_tot',
  },
  {
    factor: '52W High Dist.', weight: 20, detail: '0% (at high)→12 · 0–3%→12–20 · 3–12%=20 · 12–25%→20–6 · 25–40%→6–0 · >40%=0.',
    definition: 'How far below the 52-week high the stock is currently trading. v3.2 tent curve: rewards 3–12% below the high, penalises buying right at the 52W high (exhaustion risk) and deeply off the high.',
    why: 'v3 flipped the v2 mean-reversion curve to full credit at 0%. v3.2 refines this: right at a fresh all-time high (0%) carries exhaustion risk — a tiny pullback to 3–12% below is the sweet spot where trend is confirmed but local exhaustion is less likely. Deep retracements (>25%) lose credit as the trend weakens.',
    formula: 'dist      = (Close − max_252d_close) / max_252d_close × 100\npct_below = abs(min(dist, 0))\n0% → 12 · ramp to 20 at 3% · flat 20 at 3–12% · decay 20→6 at 12–25% · 0 beyond 40%',
  },
  {
    factor: 'Weekly RSI(14)', weight: 15, detail: '50–65=15 · 45–50 or 65–70→11 · 40–45 or 70–75→6 · 35–40+strong trend→9 · else=0.',
    definition: 'RSI computed on weekly closes (resample daily to weekly, Wilder smoothing). Medium-term momentum — a cleaner signal for multi-month DITM positions than daily RSI.',
    why: 'Weekly momentum in 50–65 confirms sustained uptrend without being overextended. Weekly RSI in 35–40 plus a confirmed trend signals a strong pullback-entry point.',
    formula: 'Resample daily Close to weekly\nWilder RSI(14) on weekly series\n50–65=15 · 45–50 or 65–70=11 · 40–45 or 70–75=6 · 35–40+Trend≥18=9 · else=0',
  },
  {
    factor: 'Chain Liquidity', weight: 15, detail: 'log10(median_OI) / log10(500) × 15 · capped at 15.',
    definition: 'Median open interest across the 0.60–0.95 delta range of the call chain. DITM options are typically less liquid than ATM — reference is 500 OI (vs 5000 for CSP/CC).',
    why: 'Illiquid DITM chains = wide spreads on entry, and difficulty exiting or rolling. Even moderate OI (500+) is sufficient for liquid fills on DITM calls.',
    formula: 'pts = min(log10(median_OI) / log10(500), 1.0) × 15',
  },
  {
    factor: 'Earnings (DTE-scaled)', weight: -15, detail: 'penalty = base × min(1, 30/dte) · ≤7d→base=−15 · 8–14d→−7 · else=0.',
    definition: 'DTE-scaled earnings penalty. A 7-day-out earnings on a 365-DTE LEAP costs ≈ −1.2 ENV; on a 30-DTE position, the full −15.',
    why: 'v2 treated earnings ≤ 7d as a hard gate (ENV = 0). For a 365-DTE LEAP, that was a category error: any IV pop reverses within a week and 358 days of thesis remain. v3 scales the penalty by remaining DTE so long-dated trades aren\'t fatally penalised by a near-term print.',
    formula: 'scale   = min(1, 30 / dte)\npenalty = -15 × scale  if days_to_earn ≤ 7\n        = -7  × scale  if days_to_earn ∈ [8, 14]\n        = 0            otherwise',
  },
  { factor: '— STRIKE SCORE (×0.5) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  {
    factor: 'Delta', weight: 20, detail: '0.82–0.90=20 · 0.75–0.82→12–20 · 0.70–0.75→0–12 · 0.90–0.95→14–20 · 0.95–1.0→9–14 · <0.70=0.',
    definition: 'Black-Scholes call delta. For DITM calls, delta is high (0.70–0.95+); the option moves nearly dollar-for-dollar with the stock.',
    why: 'v3.2 shifts the sweet spot to 0.82–0.90 (from 0.80–0.85). Higher delta reduces gamma risk and makes the position more stock-like, improving the stock-replacement thesis. Below 0.70 = not deep enough in-the-money; decay above 0.90 is gentle because very high delta is acceptable.',
    formula: 'd1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\ndelta = N(d1)',
  },
  {
    factor: 'Leverage  (v3.2)', weight: 25, detail: '2.5–4.0×=25 · 2.0–2.5×→17–25 · 1.5–2.0×→8–17 · 0–1.5×→0–8 · 4.0–5.0×→25–0 · ≥5.0×=0.',
    definition: 'leverage = delta × current_price / mid. The headline DITM metric — the actual exposure-per-dollar-deployed that stock-replacement is about.',
    why: 'v3.2 tightens the cap: flat top extended to 4× (was 3.5×), then a sharper linear drop to zero at 5× (was gradual decay to 8×). Leverage >5× almost always means a mispriced or extremely wide-spread option, not a genuinely advantaged setup — hard zero removes those from contention.',
    formula: 'leverage = delta × current_price / mid\n0–1.5×    → 0 → 8 pts\n1.5–2.0×  → 8 → 17\n2.0–2.5×  → 17 → 25\n2.5–4.0×  → 25 (full credit)\n4.0–5.0×  → 25 → 0  (sharp decay)\n≥ 5.0×    → 0  (hard zero)',
  },
  {
    factor: 'Extrinsic %', weight: 25, detail: '<2%=25 · 2–4%→19–25 · 4–6%→13–19 · 6–9%→5–13 · 9–12%→0–5 · >12%=0.',
    definition: 'Extrinsic value (time value) as a percentage of strike price. extrinsic = mid − max(price − strike, 0).',
    why: 'The entire premise of DITM: minimise the extrinsic you pay. Extrinsic is money that evaporates to theta. <2% of strike means almost all your premium is pure intrinsic — you are essentially buying stock on leverage with bounded downside. v3 drops the separate Theta% factor (audit #4: ~90% correlated with Extrinsic).',
    formula: 'intrinsic     = max(price − strike, 0)\nextrinsic     = mid − intrinsic\nextrinsic_pct = extrinsic / strike × 100',
  },
  {
    factor: 'Bid-Ask Spread', weight: 20, detail: '≤2%=20 · 2–4%→14–20 · 4–7%→7–14 · 7–12%→1–7 · >12%=0.',
    definition: '(Ask − Bid) / Mid × 100. The transaction cost paid on entry — and again on exit.',
    why: 'Wide spreads are especially costly for DITM calls because you pay them on large-notional positions. A 10% spread on a $80 DITM call costs $8 on entry alone ($16 round-trip per contract).',
    formula: 'spread_pct = (ask − bid) / mid × 100\nwhere mid = (bid + ask) / 2',
  },
  {
    factor: 'IV Percentile', weight: 10, detail: '≤25=10 · 25–50→7–10 · 50–75→3–7 · >75=0.',
    definition: 'HV-based IV percentile: % of days in the past year when IV was lower than today. The single vol-cheapness factor in v3.',
    why: 'Unlike CSP/CC (premium sellers), DITM buyers want low IV. v3 keeps IV Percentile as the only vol-cheapness factor — the v2 ENV HV Rank factor was dropped because it measured the same signal (audit #5).',
    formula: 'iv_percentile = % of last 252d where HV < today HV\nScored inversely: ≤25th pct = full marks',
  },
]

const HARD_GATES = [
  // v3 (ADR-0008) removed all v2 hard gates. Score-floor effects come from
  // the 0.85× macro-hold multiplier and DTE-scaled earnings penalty instead.
  { gate: 'Macro hold regime', effect: '× 0.85 final', reason: 'VIX ≥ 25 AND rising, OR SPY < SMA200. v3 demotes scores 15% during macro-hold instead of just displaying a banner. The directional, leveraged thesis is most fragile in exactly these regimes.' },
  { gate: 'Earnings ≤ 7 days',  effect: 'penalty −15 × min(1, 30/dte)', reason: 'DTE-scaled. A 7-day-out earnings on a 365-DTE LEAP costs ≈ −1.2 ENV; on a 30-DTE position, the full −15. v2 used a hard gate (ENV = 0) regardless of remaining DTE — fixed in v3.' },
  { gate: 'Earnings 8–14 days', effect: 'penalty −7 × min(1, 30/dte)',  reason: 'Same DTE-scaling as above; smaller base.' },
]

const SCORE_TIERS = [
  // v3: aligned with CSP/CC v3 tier scheme (75/65/55/45). v2 frontend used
  // 80/65/50/35 in legend but 75/65/55/45 in table colors (audit #11). Now consistent.
  { range: '≥ 75',  label: 'Strong',   color: '#4ade80', desc: 'Strong trend + leverage + cheap extrinsic',     action: 'Take it, normal size' },
  { range: '65–74', label: 'Solid',    color: '#86efac', desc: 'Solid setup, understand the drag',              action: 'Take it, understand the weakness' },
  { range: '55–64', label: 'Moderate', color: '#facc15', desc: 'Mechanically fine, thesis-dependent',           action: 'Only with strong conviction' },
  { range: '45–54', label: 'Weak',     color: '#fb923c', desc: 'Multiple factor drags',                         action: 'Usually skip' },
  { range: '< 45',  label: 'Avoid',    color: '#f87171', desc: 'Macro hold and/or scattered factor scores',     action: 'Skip' },
]

const DECISION_STEPS = [
  { n: 1, q: 'Score ≥ 65?',                                                   a: 'Trade it. Steps 2–4 are confirmation, not a gate. The v3 "take it" threshold is 65 — same as CSP/CC.' },
  { n: 2, q: 'Is trend confirmed? (P > SMA50 > SMA200)',                       a: 'Trend is no longer a hard gate in v3, but full alignment earns the 25 pts that anchor the ENV score. If alignment fails, your ENV will sit ~17 pts lower — read the breakdown.' },
  { n: 3, q: 'What is the leverage and the 2 biggest factor drags?',           a: 'Leverage = delta × price / mid. Sweet spot 2.5–3.5×. The lowest-scoring factors define the specific risk being priced in.' },
  { n: 4, q: 'Can I define the thesis: duration, target, and catalyst?',       a: 'If no, skip. DITM calls require a specific view — not just "bullish". Write down: entry, exit target, max loss date, catalyst window.' },
]

interface ExitNode { cond: string; action: string; tone?: 'close' | 'hold' | 'monitor' | 'assign' | 'roll' }
interface ExitBranch { label: string; children: ExitNode[] }
const EXIT_STRATEGY: ExitBranch[] = [
  {
    label: 'Profit-side (scale out)',
    children: [
      { cond: '+25% on option mid',    action: 'Consider partial close (25% of position)',   tone: 'hold' },
      { cond: '+50%',                  action: 'Close 50% — lock in most of the gain',       tone: 'close' },
      { cond: '+100%',                 action: 'Close remainder or trail the rest',          tone: 'close' },
      { cond: '+150%+',                action: 'Let the final tranche run with a stop',      tone: 'close' },
    ],
  },
  {
    label: 'Loss-side (defence)',
    children: [
      { cond: '−35% on option mid',    action: 'Hard stop — exit immediately',                       tone: 'roll' },
      { cond: 'Price breaks SMA200',   action: 'Exit — trend thesis is invalidated',                 tone: 'roll' },
      { cond: 'Score drops below 45',  action: 'Re-evaluate; close if no recovery thesis',           tone: 'monitor' },
      { cond: '120 DTE checkpoint',    action: 'Review: roll forward if score ≥ 55; close if not',   tone: 'monitor' },
    ],
  },
  {
    label: 'Macro hold regime — defensive posture',
    children: [
      { cond: 'VIX ≥ 25 and rising · or SPY < SMA200',  action: 'Scores already × 0.85 — don\'t add new exposure',           tone: 'monitor' },
      { cond: 'Existing positions in macro hold',       action: 'Tighten stops 25% · consider partial profit-take',          tone: 'close' },
    ],
  },
  {
    label: 'Roll mechanics — when ROLL is the action above',
    children: [
      { cond: 'Trigger',                                       action: 'Δ ≥ +0.95 · spot >2% above strike · price breaks SMA50 with thesis intact · ≤ 60 DTE with thesis intact', tone: 'monitor' },
      { cond: 'Target',                                        action: 'Next monthly · ~0.82 Δ at current spot · NEVER roll to a strike below cost basis · BA ≤ 5% & OI ≥ 200',  tone: 'roll' },
      { cond: 'Stop',                                          action: '>15% above original strike · 3 rolls deep · capital tied >2× original premium · no net-credit roll → close at loss',                          tone: 'close' },
    ],
  },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number, universe: UniverseKey) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
}

export function DitmInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('custom')
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
            <div className="score-legend-header">Score modifiers — v3 replaces hard gates with scaled penalties</div>
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

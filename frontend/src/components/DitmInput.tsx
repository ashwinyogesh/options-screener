import { useState, useRef, KeyboardEvent } from 'react'
import { UNIVERSE_OPTIONS, DEFAULT_UNIVERSE, universeSize, type UniverseKey } from '../constants/universes'

const PRESET_BASKET = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'AMD']

// ---------------------------------------------------------------------------
// Score legend data
// ---------------------------------------------------------------------------

// v4 (ADR-0032): cross-sectional rank-and-blend scorer. Weights below are signed
// fractional contributions (sum |w| per group ≤ group cap). Final ditm_score is
// each candidate's percentile within the universe; tier is a band on that percentile.
const SCORE_LEGEND = [
  { factor: '— VALUATION (cap 35%) —', weight: null, detail: 'Cheaper stocks rank higher.', definition: '', why: '', formula: '' },
  {
    factor: 'P/S TTM', weight: -14.6, detail: 'Cheaper price-to-sales wins.',
    definition: 'Trailing-twelve-month price-to-sales multiple from EDGAR. The richest single sales-multiple signal in the audit.',
    why: 'Cheap on sales is the most robust value signal across sectors and is least gameable by accounting. Top IC of the 13 factors (|IC|≈0.10).',
    formula: 'ps_ttm = market_cap / revenue_ttm',
  },
  {
    factor: 'EV/Sales', weight: -12.9, detail: 'Cheaper EV-to-sales wins.',
    definition: 'Enterprise value (mkt cap + debt − cash) / TTM revenue. Capital-structure-neutral version of P/S.',
    why: 'Captures cheapness for levered names where P/S misleads.',
    formula: 'ev_sales = (mcap + total_debt − cash) / revenue_ttm',
  },
  {
    factor: 'EV/EBITDA', weight: -7.5, detail: 'Cheaper EV-to-cash-flow wins.',
    definition: 'Enterprise value / TTM EBITDA. Cash-flow-multiple signal.',
    why: 'Adds a cash-flow dimension to the two sales-based factors. Lower IC than P/S but de-correlated.',
    formula: 'ev_ebitda = ev / ebitda_ttm',
  },

  { factor: '— CAPITAL STRUCTURE (cap 15%) —', weight: null, detail: 'Modest leverage rewarded.', definition: '', why: '', formula: '' },
  {
    factor: 'Debt / Equity', weight: 9.7, detail: 'More leverage scores higher.',
    definition: 'Total debt / book equity from latest filing.',
    why: 'In the audit, modest leverage correlated positively with forward DITM ROC — companies that lever up tend to be the ones with cash-flow visibility.',
    formula: 'd_e = total_debt / total_equity',
  },
  {
    factor: 'Net Debt / EBITDA', weight: 5.3, detail: 'More leverage scores higher (capped).',
    definition: '(Total debt − cash) / TTM EBITDA. Years of cash flow needed to retire debt.',
    why: 'Same direction as D/E but adds a cash-flow constraint.',
    formula: 'nd_ebitda = (debt − cash) / ebitda_ttm',
  },

  { factor: '— TECHNICAL (cap 20%) —', weight: null, detail: 'Mean-reverting + selective momentum mix.', definition: '', why: '', formula: '' },
  {
    factor: 'Weekly RSI(14)', weight: -7.6, detail: 'Oversold names win (contrarian).',
    definition: 'Wilder RSI on weekly closes.',
    why: 'In the v4 audit, low weekly RSI (oversold) was the strongest technical predictor of forward DITM ROC. Contrarian to v3.',
    formula: 'wk_rsi = Wilder-RSI(weekly_close, 14)',
  },
  {
    factor: '52W High Distance', weight: -4.3, detail: 'Deeper pullbacks score better.',
    definition: 'Negative pct gap from the 252-day high (e.g. −10% means 10% below the high).',
    why: 'Mean-reversion signal: deeper pullbacks within ongoing names price in better forward returns. Inverted from v3.',
    formula: 'dist52w = (close − max_252d) / max_252d × 100',
  },
  {
    factor: 'HV30', weight: -4.6, detail: 'Calmer stocks (lower realised vol) win.',
    definition: '30-day realised volatility (annualised %).',
    why: 'Lower realised vol stocks compounded better forward DITM ROC, net of all other factors.',
    formula: 'hv30 = std(log_returns_30d) × sqrt(252) × 100',
  },
  {
    factor: '200d Return', weight: 3.4, detail: 'Mild long-term momentum bonus.',
    definition: '200-day median-anchored return.',
    why: 'Residual momentum after the contrarian RSI/52W signals are factored in. Small but positive.',
    formula: 'ret_200d = close / median(close[-205:-200]) − 1',
  },

  { factor: '— MACRO (cap 5%) —', weight: null, detail: 'Sector relative-strength tilt (when wired).', definition: '', why: '', formula: '' },
  {
    factor: 'Sector RS 6m', weight: -5.0, detail: 'Lagging sectors win (currently inert).',
    definition: '6-month relative strength of the stock\u2019s GICS sector vs SPY.',
    why: 'Lagging sectors mean-revert better than leaders over the DITM horizon. Currently logged as None (rank-neutral) — sector-RS feed pending.',
    formula: 'sector_rs_6m = sector_etf.ret_126d − spy.ret_126d',
  },

  { factor: '— OPTION CHAIN (cap 25%) —', weight: null, detail: 'Trade-mechanics edge.', definition: '', why: '', formula: '' },
  {
    factor: 'Leverage', weight: 9.4, detail: 'More leverage (δ×spot/mid) wins.',
    definition: 'delta × spot / mid. The headline DITM number — exposure per dollar deployed.',
    why: 'Top option-side IC in the audit. Stock-replacement only works when leverage is meaningful (typically 2.0–4.0×).',
    formula: 'leverage = delta × current_price / mid',
  },
  {
    factor: 'Delta', weight: 8.0, detail: 'Deeper-in-the-money wins.',
    definition: 'Black-Scholes call delta.',
    why: 'High delta keeps the position stock-like and reduces gamma whipsaw. Independent residual value over Leverage.',
    formula: 'd1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\ndelta = N(d1)',
  },
  {
    factor: 'Extrinsic %', weight: -7.6, detail: 'Less time value paid wins.',
    definition: '(mid − intrinsic) / strike × 100.',
    why: 'Extrinsic is the slice that bleeds to theta. The whole DITM premise is to minimise it.',
    formula: 'intrinsic     = max(close − strike, 0)\nextrinsic     = mid − intrinsic\nextrinsic_pct = extrinsic / strike × 100',
  },
]

const HARD_GATES = [
  // v4 (ADR-0032) replaces v3 score modifiers with universe-relative ranking.
  // The macro-hold multiplier and DTE-scaled earnings penalty are removed:
  // sector RS handles regime drift, and earnings risk is selected at trade entry, not in the score.
  { gate: 'Earnings within DTE', effect: 'badge only (display)', reason: 'Surfaced in the table for awareness; no longer scored. Pick around earnings at the trader level — DITM LEAPs ride through prints.' },
  { gate: 'Min factors observed', effect: 'tier = E if < 8 of 13 observed', reason: 'A candidate must have at least 8 of the 13 factors populated to receive a non-bottom tier. Ensures sparse fundamentals can\u2019t score above-rank by missing the bad factors.' },
]

const SCORE_TIERS = [
  // v4: A/B/C/D/E percentile bands. Score is the candidate\u2019s percentile within the universe.
  { range: '≥ 90',  label: 'A',  color: '#4ade80', desc: 'Top decile across val, capital, technical, and chain factors.',     action: 'Take it, normal size' },
  { range: '70–89', label: 'B',  color: '#86efac', desc: 'Strong all-round; one or two pillars merely average.',                action: 'Take it, sized to conviction' },
  { range: '50–69', label: 'C',  color: '#facc15', desc: 'Mechanically OK but a clear pillar drag.',                            action: 'Only with a thesis' },
  { range: '30–49', label: 'D',  color: '#fb923c', desc: 'Multiple weak pillars or sparse fundamentals.',                       action: 'Usually skip' },
  { range: '< 30',  label: 'E',  color: '#f87171', desc: 'Bottom of the universe and/or below the min-factors floor.',          action: 'Skip' },
]

const DECISION_STEPS = [
  { n: 1, q: 'Tier A or B?',                                                  a: 'Trade it. The v4 tier captures the cross-sectional consensus across 13 factors — these are the universe-best setups.' },
  { n: 2, q: 'Is the dominant pillar sane for the regime?',                  a: 'Look at env_detail (Val/Cap/Macro) vs strike_detail (Tech/Opt). If everything is riding on Option-chain edge with weak Valuation, you are betting on cheap optionality only.' },
  { n: 3, q: 'What are the 2 biggest factor drags?',                          a: 'The Drags column shows the lowest-contribution factors after sign. Read them as: \u201cthis trade is paying for X and Y to be merely average.\u201d' },
  { n: 4, q: 'Can I define the thesis: duration, target, and catalyst?',     a: 'If no, skip. DITM calls require a specific view \u2014 not just \u201cbullish\u201d. Write down: entry, exit target, max loss date, catalyst window.' },
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
      { cond: 'Score drops below D (30)',  action: 'Re-evaluate; close if no recovery thesis',           tone: 'monitor' },
      { cond: '120 DTE checkpoint',    action: 'Review: roll forward if tier still B or better; close if not',   tone: 'monitor' },
    ],
  },
  {
    label: 'Macro context — read the regime before sizing',
    children: [
      { cond: 'VIX ≥ 25 and rising · or SPY < SMA200',  action: 'v4 no longer multiplies the score — cut size yourself in chop',           tone: 'monitor' },
      { cond: 'Sector RS pillar deeply negative',      action: 'Confirm thesis isn’t fighting a clear sector downtrend',                   tone: 'monitor' },
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
                &nbsp;Roll forward if tier is still B or better and thesis holds. Exit if thesis is broken.
              </span>
            </div>
          </div>

          {/* Hard gates */}
          <div className="score-legend-factors">
            <div className="score-legend-header">Score modifiers — v4 ranks the universe; very few hard rails remain</div>
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
            <div className="score-legend-header">Score breakdown — how each factor influences the final tier</div>
            <div style={{ padding: '6px 8px', marginBottom: '6px', background: '#0f172a', borderRadius: '5px', fontSize: '11px', color: '#94a3b8', borderLeft: '3px solid #334155', lineHeight: 1.5 }}>
              <strong style={{ color: '#cbd5e1' }}>How to read this:</strong> the percentage is the factor’s <strong>share of the final score</strong> (bigger = more influence). The arrow shows which direction wins: <strong>↑ higher input scores higher</strong> &nbsp;·&nbsp; <strong>↓ lower input scores higher</strong>. Neither direction is "good" or "bad" — both contribute equally to the score. Click any factor for the formula.
            </div>
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
                        style={{ color: '#cbd5e1' }}
                        title={f.weight >= 0 ? 'Higher input scores higher' : 'Lower input scores higher'}
                      >
                        {Math.abs(f.weight).toFixed(1)}% {f.weight >= 0 ? '↑' : '↓'}
                      </span>
                      <div className="score-factor-bar-wrap">
                        <div className="score-factor-bar" style={{
                          width: `${Math.min(Math.abs(f.weight) / 15 * 100, 100)}%`,
                          background: '#60a5fa'
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
              <strong style={{ color: '#94a3b8' }}>Tie-break:</strong> within a tier, highest <strong>option-pillar percentile</strong> wins, then closest to <strong>0.85 delta</strong>.
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

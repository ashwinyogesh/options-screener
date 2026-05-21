import { useState, useRef, KeyboardEvent } from 'react'
import { UNIVERSE_OPTIONS, DEFAULT_UNIVERSE, universeSize, type UniverseKey } from '../constants/universes'

const PRESET_BASKET = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'AMD']

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.4) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'IV Percentile',   weight: 60,  detail: '',
    definition: '% of the last 252 trading days where the 30-day Historical Volatility was lower than today\u2019s. Measures whether options are elevated relative to this stock\u2019s own recent history.',
    why: 'v3.4 (Method D, ADR-0011) raised IVP from 35 to 60 pts after a 7,085-trade CSP backtest showed IVP had the strongest positive relationship to realised ROC of any env factor — the others (SMA/SLP/RSI) were ~0 or negative and were dropped. Regime-agnostic: a stable stock during its own elevated-IV period scores well even if absolute IV is near 1.0.',
    formula: 'hv = rolling_30d_std(log_returns) \u00d7 sqrt(252)\n  iv_percentile = % of last-252d where hv[t] < hv[today]\n  Curve (v3.3 shape \u00d7 60/35):\n  <30th=0 \u00b7 30\u201350th\u21920\u219217.1 \u00b7 50\u201375th\u219217.1\u219242.9\n  75\u201390th\u219242.9\u219260 \u00b7 \u226590th=60' },
  { factor: 'Trend: 52W Distance',  weight: 20,  detail: '',
    definition: 'CSP-only factor measuring how far the current price sits below its 52-week (252 trading-day) high. v3.4 Method D flipped the direction: now rewards distance FROM the high (mean-reversion sweet spot), not proximity to it.',
    why: 'The v3.3 CSP assumption ("close to highs = strong stock = puts stay safe") was empirically wrong-signed. In the 7,085-trade backtest, names near 52W highs had WORSE realised ROC and larger loss-given-assignment ($\u22121,509 vs $\u2212955). Names well below highs gave better outcomes \u2014 oversold mean reversion outperformed momentum chasing for cash-secured puts.',
    formula: 'dist = (Close[t] \u2212 max(Close, 252d)) / max(Close, 252d) \u00d7 100\n  pct_below = abs(min(dist, 0))\n  v3.4 Method D (flipped):\n  pct_below \u22645% \u2192 0   \u00b7   5\u201330% linear 0\u219220   \u00b7   >30% \u2192 20' },
  { factor: 'Chain Median OI', weight: 20,  detail: '',
    definition: 'Median open interest across all put strikes in the 0.10\u20130.40 delta range \u2014 a measure of how actively traded the chain is.',
    why: 'Thin chains mean wide spreads on entry and difficulty rolling if the trade moves against you. Liquid chains = trade near fair value, clean exits.',
    formula: 'pts = min(log10(chain_median_oi) / log10(5000), 1.0) \u00d7 20\n  Unchanged from v3.3.' },
  { factor: 'Earnings in DTE', weight: -15, detail: '',
    definition: 'Binary flag \u2014 true if the next earnings announcement falls within the option expiry window.',
    why: 'Earnings create overnight gap risk that can blow through your strike regardless of technicals. The most common cause of unexpected assignment on otherwise sound CSP setups.',
    formula: 'earnings_within_dte = True if:\n  0 \u2264 (earnings_date \u2212 today).days \u2264 DTE\n  Source: yfinance calendarEvents.earnings' },
  { factor: '— STRIKE SCORE (×0.6) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'Delta',            weight: 40,  detail: '',
    definition: 'Rate of change of the option price per $1 move in the stock. For puts, |\u0394| approximates the market-implied probability of finishing in-the-money.',
    why: 'v3.4 Method D raised \u0394 from 25 to 40 pts \u2014 backtest showed \u0394 was the strongest predictor of capital safety (closer to the ideal \u22120.225 \u2192 materially lower assignment rate and smaller loss-given-assignment).',
    formula: 'Black-Scholes put delta:\n  d1 = (ln(S/K) + (r + 0.5\u03c3\u00b2)T) / (\u03c3\u221aT)\n  delta = N(d1) \u2212 1\n  Smooth bell, ideal = \u22120.225 (CSP). Bands (v3.3 shape \u00d7 40/25):\n  |\u0394\u2212ideal| \u22640.025 \u2192 40\n  0.025\u20130.075 \u2192 40\u219225.6\n  0.075\u20130.125 \u2192 25.6\u219214.4\n  0.125\u20130.175 \u2192 14.4\u21920' },
  { factor: 'Bid-Ask Spread',  weight: 15,  detail: '',
    definition: 'Percentage spread between bid and ask relative to the option mid: (ask\u2212bid)/mid \u00d7 100.',
    why: 'v3.4 lowered BA from 25 to 15 pts. Wide spreads still erode realised premium, but in the backtest BA had a much smaller effect on realised ROC than \u0394 \u2014 weight redistributed.',
    formula: 'spread_pct = (ask \u2212 bid) / mid \u00d7 100\n  Curve (v3.3 shape \u00d7 15/25):\n  \u22641%=15 \u00b7 1\u20133%\u219215\u219210.2 \u00b7 3\u20135%\u219210.2\u21925.4\n  5\u20138%\u21925.4\u21921.2 \u00b7 >8%=0' },
  { factor: 'OI / Volume',      weight: 15,  detail: '',
    definition: 'Per-strike circuit breaker. Uses today\u2019s volume when market is open, otherwise open interest at this strike.',
    why: 'High OI/volume at this specific strike = efficient price discovery, fast fills near mid, and a liquid exit. Unchanged in v3.4.',
    formula: 'liquidity_count = volume (market open) | open_interest (closed)\n  \u22651000=15 \u00b7 500\u20131000\u219210.5\u219215\n  200\u2013500\u21926\u219210.5 \u00b7 100\u2013200\u21920\u21926 \u00b7 <100=0' },
  { factor: 'Annualized ROC',   weight: 30,  detail: '',
    definition: 'Annualized return on capital for a cash-secured put. Premium collected vs. cash tied up, normalized to one year.',
    why: 'v3.4 lowered ROC from 35 to 30 pts. ROC chasing was rewarding into-the-money overrides in the backtest \u2014 reducing the cap and raising \u0394 produced a Spearman rank \u03c1 of +0.475 against realised ROC (vs +0.229 under v3.3).',
    formula: 'capital_per_share = strike \u2212 credit\n  ROC = (credit / capital_per_share) \u00d7 (365 / DTE) \u00d7 100\n  Curve (v3.3 shape \u00d7 30/35, ceiling 12%):\n  \u226512%=30 \u00b7 8\u201312%\u219221\u219230 \u00b7 4\u20138%\u219212\u219221\n  2\u20134%\u21923\u219212 \u00b7 1\u20132%\u21920\u21923 \u00b7 <1%=0' },
]

// v3.4 Method D recalibration — bands derived from n=18,016 backtest trades
// (ρ=+0.49, 3y full 154-ticker universe, 35 DTE). Cliffs at the decile boundaries:
//   ≥87:   mean ROC +30%, win 91%, assign 14% — clear top decile
//   79–86: mean ROC +17%, win 86%
//   69–78: mean ROC  +9%, win 83% — first profitable band
//   51–68: mean ROC  −4%, win 78% — assignment tail wipes out small credits
//   <51:   mean ROC  −4%, win 77%, worst $ PnL — bottom decile
const SCORE_TIERS = [
  { range: '≥ 87', label: 'Excellent',     color: '#4ade80', desc: 'Top decile — best win rate (91%), lowest assignment (14%)', action: 'Take it, size up if conviction matches thesis' },
  { range: '79–86', label: 'Strong',        color: '#86efac', desc: 'Strong setup, mean ROC +17%, win 86%',                action: 'Take it, normal size' },
  { range: '69–78', label: 'Good',          color: '#bef264', desc: 'First profitable band — mean ROC +9%, win 83%',       action: 'Take it, understand the weakest factor' },
  { range: '51–68', label: 'Marginal',      color: '#facc15', desc: 'Mean ROC negative — high win rate masks tail assignment losses', action: 'Only with a documented directional thesis' },
  { range: '< 51',  label: 'Skip',          color: '#f87171', desc: 'Bottom decile — worst $ PnL, drag outweighs premium',  action: 'Skip' },
]

const DECISION_STEPS = [
  { n: 1, q: 'Score ≥ 69?',                                       a: 'Take it on score alone (modulo step 2). 51–68 needs a documented thesis; <51 skip.' },
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
  {
    label: 'Roll mechanics — when ROLL is the action above',
    children: [
      { cond: 'Trigger — act when ANY fires',  action: 'abs(Δ) ≥ 0.40 · spot within 2% of strike · ≥ 50% premium captured with > 21 DTE · ≤ 21 DTE with abs(Δ) ≥ 0.30 · thesis break (earnings pre-announce, support breach)', tone: 'monitor' },
      { cond: 'Target — down-and-out, net credit', action: 'Next monthly expiry · new strike near −0.225 Δ at current spot · must be net credit · chain BA ≤ 5% & OI ≥ 200', tone: 'roll' },
      { cond: 'Stop — stop rolling when ANY fires', action: 'Roll target > 15% below original strike · 3 rolls deep · capital tied > 2× original · no net-credit roll available → take assignment & wheel into a CC', tone: 'close' },
    ],
  },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number, universe: UniverseKey, maxCapital?: number) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number, maxCapital?: number) => void
  loading: boolean
}

export function CspInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('custom')
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
  const [maxCapital, setMaxCapital] = useState<number | ''>('')
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
    if (maxCapital !== '' && (!Number.isFinite(maxCapital) || maxCapital < 100)) return
    onScan(topN, scanMinDTE, scanMaxDTE, universe, maxCapital !== '' && Number.isFinite(maxCapital) ? maxCapital : undefined)
  }

  function handleCustomSubmit() {
    let err: string | null = null
    if (minDTE > maxDTE) err = 'Min DTE must be \u2264 Max DTE'
    else if (minDTE < 1 || maxDTE > 90) err = 'DTE must be between 1 and 90'
    else if (maxCapital !== '' && (!Number.isFinite(maxCapital) || maxCapital < 100)) err = 'Max Capital must be at least $100'
    setDteError(err)
    if (err) return

    const allSymbols = inputValue.trim()
      ? [...chips, ...inputValue.split(/[\s,]+/).filter(Boolean)]
      : chips
    const unique = [...new Set(allSymbols.map(s => s.trim().toUpperCase()).filter(Boolean))]
    if (unique.length === 0) return
    onCustom(unique.slice(0, 20), minDTE, maxDTE, maxCapital !== '' && Number.isFinite(maxCapital) ? maxCapital : undefined)
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
            <label className="filter-item">
              Max Capital ($)
              <input
                type="number"
                className="dte-input"
                value={maxCapital}
                placeholder="No limit"
                min={100}
                step={100}
                onChange={e => setMaxCapital(e.target.value === '' ? '' : Number(e.target.value))}
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
              <label>
                Max Capital ($)
                <input
                  type="number"
                  className="dte-input"
                  value={maxCapital}
                  placeholder="No limit"
                  min={100}
                  step={100}
                  onChange={e => setMaxCapital(e.target.value === '' ? '' : Number(e.target.value))}
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

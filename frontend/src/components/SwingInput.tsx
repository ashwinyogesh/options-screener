import { useState } from 'react'
import type { KeyboardEvent } from 'react'

interface Props {
  onScan: (topN: number, universe: string) => void
  onCustom: (symbols: string[], bypassGates: boolean) => void
  loading: boolean
}

const UNIVERSE_KEY = 'swing_eligible'
const UNIVERSE_LABEL = 'Swing-eligible (~200)'
const UNIVERSE_HINT = '$500M+ mcap, 500K+ ADV — incl. high-beta movers (ASTS, RKLB, MU, IONQ, MSTR…)'

interface ScoreFactor {
  factor: string
  weight: number | null
  detail?: string
  definition?: string
  why?: string
  formula?: string
}

const SCORE_TIERS = [
  { range: '≥ 75', label: 'High',          color: '#4ade80', desc: 'Strong R:R with confirming context + institutional',  action: 'Take it, normal size' },
  { range: '65–74', label: 'Solid',         color: '#86efac', desc: 'Clean setup, R:R ≥ 3 typical',                       action: 'Take it; honor stop' },
  { range: '55–64', label: 'Medium',        color: '#fbbf24', desc: 'Workable but mixed context or weak institutional',   action: 'Smaller size; tighter stop' },
  { range: '45–54', label: 'Speculative',   color: '#fb923c', desc: 'Setup OK but R:R thin or trend uncertain',           action: 'Skip unless thesis is strong' },
  { range: '< 45',  label: 'Skip',          color: '#f87171', desc: 'Below quality bar after hard gates',                 action: 'Skip' },
]

const SETUP_GUIDE = [
  {
    name: 'Breakout',
    hold: '5–10d',
    color: '#60a5fa',
    signals: 'Tight base ≥ 7d (range ≤ 8%) · 1.5× volume surge · structure-high reclaim · BB squeeze <25p',
    thesis: 'Compression releases; ride the expansion. Stop below the base low.',
  },
  {
    name: 'Momentum',
    hold: '7–14d',
    color: '#4ade80',
    signals: 'EMA stack 7/9 · ADX ≥ 22 with +DI dominant · RS vs SPY > 1.1 · MACD zero-cross',
    thesis: 'Trend in motion stays in motion. Trail behind the 8/21 EMA.',
  },
  {
    name: 'Reversion',
    hold: '3–7d',
    color: '#fbbf24',
    signals: 'RSI < 30 · Stoch %K < 20 · bullish RSI divergence · 0.618 fib hold · above 200 EMA',
    thesis: 'Oversold bounce in a still-intact uptrend. Quick exit; tight stop under the swing low.',
  },
  {
    name: 'Retest',
    hold: '10–21d',
    color: '#a78bfa',
    signals: 'Structure reclaim 5–20d ago · new base forming above the level · RS holding ≥ 1.0',
    thesis: 'Second leg after a breakout cools off. Stop below the reclaimed level.',
  },
]

const GATES = [
  { gate: 'R:R',                threshold: '≥ 2.5 / 2.75 / 3.0',  note: 'Dynamic per regime — risk_on / neutral / risk_off' },
  { gate: 'Setup score',        threshold: '≥ 40 / 100',         note: 'Below this, no setup is well-formed enough' },
  { gate: 'ADV (dollar)',       threshold: '≥ $5,000,000',       note: 'Liquidity for clean exits' },
  { gate: 'Price',              threshold: '≥ $5',               note: 'Avoid sub-$5 chop' },
  { gate: 'OHLC history',       threshold: '≥ 200 bars',         note: '~10 months — required for EMA 200 reliability and reversion safety guard' },
  { gate: 'Stop distance',      threshold: '≤ 50% of entry',     note: 'Wider stop = structurally invalid setup' },
  { gate: 'Earnings — any setup',     threshold: 'days_to_earnings > 1', note: '≤ 1 day to earnings → excluded outright' },
  { gate: 'Earnings — reversion',     threshold: 'days_to_earnings > 7', note: 'Reversion ≤ 7 days from earnings → excluded' },
  { gate: 'Regime-disabled setups',   threshold: 'setup ∉ regime.disable_setups', note: 'Reversion blocked in risk_off regime' },
]

const SCORE_BREAKDOWN: ScoreFactor[] = [
  // ─── R:R bucket (40 pts) ─────────────────────────────────────────────
  { factor: 'R:R · 40 pts max', weight: null },
  {
    factor: 'Reward-to-Risk ratio',
    weight: 40,
    detail: '2.5 → 0 · 3.0 → 25 · 4.0 → 35 · ≥ 5.0 → 40 (piecewise-linear)',
    definition: ': (target − trigger) / (trigger − stop). Trigger and stop are setup-specific and structural — breakout uses base_high / base_low, momentum uses EMA8 pullback, retest uses the reclaimed level, reversion uses current price with the swing-low stop. R:R reflects the trade you take at the **proper entry**, not at chase prices.',
    why: ': Across thousands of swing trades, R:R is the strongest single predictor of profitability — more than entry quality. A 50% win rate at 3R still doubles equity; a 70% win rate at 1.5R barely breaks even. Computing it off the trigger (not the current close) prevents the screener from over-penalizing extended names that are otherwise well-formed.',
    formula: 'trigger, stop = build_trigger(setup, features)\n\n# Target (v2.3): ATR projection is the credibility ceiling\natr_target = trigger + ATR_MULT[setup] × atr14\n             # breakout=3.0 · momentum=2.5 · reversion=2.0 · retest=3.5\nrr_floor   = trigger + R_MULT[setup] × risk\n             # breakout=3.0 · momentum=2.75 · reversion=2.5 · retest=3.25\ntarget     = min(atr_target, rr_floor)\n# If stop is wide, ATR can\'t support the R:R floor → R:R drops below gate → filtered out\n\nrr = (target - trigger) / (trigger - stop)\nrr_pts(2.5)  = 0\nrr_pts(3.0)  = 25\nrr_pts(4.0)  = 35\nrr_pts(5.0+) = 40\n# CHASING flag if current_price > trigger * 1.03',
  },

  // ─── Setup quality bucket (30 pts) ───────────────────────────────────
  { factor: 'Setup quality · 30 pts max', weight: null },
  {
    factor: 'best_setup score',
    weight: 30,
    detail: 'max(breakout, momentum, reversion, retest) × 0.30',
    definition: ': Each of four setup detectors scores the chart 0–100 by counting confirmed signals. The highest-scoring setup is the trade thesis; its raw 0–100 → 0–30 pts here.',
    why: ': A trade only makes sense if the geometry is well-formed. A 90/100 momentum setup is qualitatively different from a 45/100 one — the second has fewer confirmations and fades faster.',
    formula: 'setup_pts = min(30, best_setup_score * 0.30)\n# hard gate: best_setup_score >= 40 to be screened',
  },
  {
    factor: '↳ Breakout signals',
    weight: 0,
    detail: 'tight base ≥ 7d (range ≤ 8%) · 1.5× volume surge · structure-high reclaim · BB squeeze < 25 pct',
    definition: ': Consolidation-then-expansion pattern. Looks for a low-range base (recent N-day high vs low within 8%) that suddenly breaks higher on above-average volume.',
    why: ': Volatility compresses before it expands. The squeeze + surge combo filters noise from real institutional accumulation breaking out.',
    formula: 'base_range = (max(highs[-7:]) - min(lows[-7:])) / close\nvol_surge = volume[-1] / avg_volume(20)\nbb_squeeze = percentile_of(bb_width, lookback=120)\n# all three must trigger for max signal',
  },
  {
    factor: '↳ Momentum signals',
    weight: 0,
    detail: 'EMA stack 8/21/50 aligned · ADX ≥ 22 with +DI > −DI · RS vs SPY > 1.1 · MACD ≥ 0',
    definition: ': Trend-in-motion check. EMA alignment (price > 8 > 21 > 50) confirms structure; ADX confirms strength; +DI/−DI confirms direction; RS confirms outperformance.',
    why: ': "Trend in motion stays in motion" only when all four agree. ADX without alignment = chop in a fake trend; alignment without ADX = drifting tape.',
    formula: 'ema_stack = price > ema8 > ema21 > ema50\nadx14 >= 22 AND plus_di > minus_di\nrs = (stock_pct_chg_20d) / (spy_pct_chg_20d) > 1.1\nmacd_histogram >= 0',
  },
  {
    factor: '↳ Reversion signals',
    weight: 0,
    detail: 'RSI < 30 · Stoch %K < 20 · bullish RSI divergence · 0.618 fib hold · close > EMA 200',
    definition: ': Oversold-bounce-inside-uptrend pattern. The 200 EMA filter is critical — without it this becomes "catch a falling knife".',
    why: ': Mean-reversion only edges in regimes where the long-term trend is intact. RSI divergence + fib hold provide a structural floor.',
    formula: 'rsi14 < 30 AND stoch_k < 20\nrsi_div = price makes lower low AND rsi makes higher low\nfib_618 = retracement holds 0.618 of last impulse\nclose > ema200',
  },
  {
    factor: '↳ Retest signals',
    weight: 0,
    detail: 'structure reclaim 5–20 bars ago · new base forming above the level · RS ≥ 1.0',
    definition: ': Second-leg setup after a prior breakout cooled off. Price reclaims a former resistance, pulls back to it, and builds a tighter base — the "handle" after the cup.',
    why: ': The cleanest entry in technical analysis. The prior level acts as confirmed support; risk is well-defined just below it.',
    formula: 'reclaim_bar in [5..20] bars ago\ncurrent base_range < initial breakout base_range\nclose > reclaim_level AND rs >= 1.0',
  },

  // ─── Context bucket (20 pts) ─────────────────────────────────────────
  { factor: 'Context · 20 pts max', weight: null },
  {
    factor: 'ADX trend strength',
    weight: 10,
    detail: '< 15 → 0 · 15–22 → linear 3–7 · 22–30 → linear 7–10 · ≥ 30 → 10',
    definition: ': Average Directional Index (14-period). Measures the strength of any directional trend, regardless of direction. ADX below 20 = no real trend; above 25 = established; above 30 = strong.',
    why: ': Setup detectors confirm pattern structure; ADX confirms the market is actually trending into that structure. A beautiful breakout in a 10-ADX chop zone typically fades. Not already scored inside any detector, so this is a genuinely orthogonal context signal.',
    formula: 'tr = max(high-low, |high-prev_close|, |low-prev_close|)\natr = ema(tr, 14)\nplus_dm, minus_dm = directional movement\nadx = 100 * ema(|plus_di - minus_di| / (plus_di + minus_di), 14)\n# <15→0 | 15–22→linear 3–7 | 22–30→linear 7–10 | ≥30→10',
  },
  {
    factor: 'A/D line slope',
    weight: 10,
    detail: '20-bar % slope · < 0 → 0 · 0–5% → linear 0–10 · ≥ 5% → 10',
    definition: ': Accumulation/Distribution line is a cumulative volume-weighted closing-bias indicator. Up-slope = closes tending to bar highs on rising volume = institutional accumulation.',
    why: ': Proxies for "is real money quietly buying". A flat or falling A/D slope behind a flashy price chart usually means the move is retail-driven and short-lived. Moved from the old institutional bucket to context at double weight (5→10 pts) because it measures market-level participation, not stock-level ownership.',
    formula: 'mfm = ((close-low) - (high-close)) / (high-low)\nad = cumsum(mfm * volume)\nslope_pct = (ad[-1] - ad[-20]) / abs(ad[-20]) * 100\n# <0→0 | 0–5%→linear 0–10 | ≥5%→10',
  },

  // ─── Institutional bucket (10 pts) ───────────────────────────────────
  { factor: 'Institutional · 10 pts max', weight: null },
  {
    factor: 'Consecutive higher lows',
    weight: 5,
    detail: '0 → 0 · 1 → 2 · 2 → 4 · ≥ 3 → 5',
    definition: ': Counts the number of consecutive swing lows where each is above the previous (over the last 20 bars). Confirms that buyers are absorbing supply at successively higher prices.',
    why: ': The cleanest hand of \u201caccumulation without fanfare\u201d. Three consecutive higher lows with no higher high means someone is quietly stepping in. Already scored in the momentum detector (where it fires directly); at 5 pts here it gives a small universal credit across all setup types without dominating.',
    formula: 'lows = rolling_min(low, window=3)\nhigher_lows = count consecutive steps where lows[i] > lows[i-1]\n# 0→0 | 1→2 | 2→4 | ≥3→5',
  },
  {
    factor: 'Institutional ownership %',
    weight: 5,
    detail: '< 40% → 0 · 40% → 0 · 70% → 5 (linear, then capped)',
    definition: ': yfinance `heldPercentInstitutions` snapshot — share of float held by 13F filers. A coarse proxy in the absence of dark-pool prints.',
    why: ': High institutional ownership = the name is on real radar screens. Below 40% it\'s either too small or retail-dominated; above 70% adds confidence the setup will see follow-through buyers.',
    formula: 'inst = info.get("heldPercentInstitutions") * 100\n# <40→0, 70+→5, in between linear',
  },

  // ─── Cross-bucket multipliers (v2.0.0 — applied AFTER the additive sum) ─
  { factor: 'Cross-bucket multipliers · final = raw × regime × earnings × extended', weight: null },
  {
    factor: 'Regime multiplier',
    weight: 0,
    detail: 'risk_on → 1.0 · neutral → ~0.76–0.86 · risk_off → 0.6–0.76',
    definition: ': One global RegimeState computed per scan from SPY trend (35 wt), VIX 1y percentile (25 wt), universe breadth (25 wt), and IWM/SPY 20d RS (15 wt). Multiplier = 0.6 + 0.4 × risk_on_score / 100, clamped [0.6, 1.0].',
    why: ': A 2.5 R:R breakout in a VIX-12 calm-bull tape is a different bet from the same setup in a VIX-30 risk-off tape. Multiplying instead of adding lets the environment override an otherwise-clean setup, but never erase it. In risk_off the dynamic gate rises to 3.0 and reversion is mechanically excluded.',
    formula: 'index_score   = bull→100, neutral→65 or 35, bear→0\nvol_score     = calm→100, normal→70, elevated→30, shock→0\nbreadth_score = pct_above_ema50 (0–100)\nra_score      = IWM/SPY 20d return diff (arithmetic), mapped -5pp→0, 0pp→50, +5pp→100\nrisk_on_score = (35*idx + 25*vol + 25*brd + 15*ra) / 100\nmultiplier    = clamp(0.6 + 0.4 * risk_on_score / 100, 0.6, 1.0)\nrr_gate       = 2.5 if risk_on else 2.75 if neutral else 3.0',
  },
  {
    factor: 'Earnings multiplier',
    weight: 0,
    detail: '≤ 3d → 0.50 · 4–7d → 0.75 · 8–14d → 0.90 · > 14d / unknown → 1.00',
    definition: ': Graduated haircut on proximity to next earnings report (yfinance `earningsDate`). Applied alongside hard gates: ≤ 1 day to any setup is excluded outright; ≤ 7 days for reversion is excluded outright. Hold window is also trimmed to (days_to_earnings − 1) when it would otherwise span the event (`forced_short_hold` flag).',
    why: ': IV crush + binary-outcome risk scale non-linearly with proximity. A T-2 setup is qualitatively different from T-7. Fixed 0.5 floor because some near-earnings trades genuinely work (post-results continuation), but they should never rank alongside clean-tape setups.',
    formula: 'def earnings_factor(dte):\n    if dte is None or dte < 0: return 1.0\n    if dte <= 3:  return 0.50\n    if dte <= 7:  return 0.75\n    if dte <= 14: return 0.90\n    return 1.0',
  },
  {
    factor: 'Extended (CHASING) multiplier',
    weight: 0,
    detail: 'current price > 3% past structural trigger → 0.70, else 1.00',
    definition: ': Per-setup structural triggers (breakout=base_high, momentum=EMA8, retest=reclaim_level, reversion=current_price). When current price is more than 3% past the trigger, the setup is "extended" — a chase entry, not a proper one.',
    why: ': R:R is computed off the *trigger*, not the current close, so a chase doesn\'t hurt R:R points. The extended multiplier penalises the chase directly so the screener doesn\'t reward "looks great if you got in two days ago" setups.',
    formula: 'trigger = build_trigger(setup, features)\nextended = current_price > trigger * 1.03\nextended_factor = 0.7 if extended else 1.0',
  },
]

const PLAYBOOK = [
  { n: 1, q: 'What\'s the current regime?',          a: 'Check the banner above the table. risk_on → trade actively; neutral → be selective; risk_off → take only the best, and reversion is auto-blocked.' },
  { n: 2, q: 'Score ≥ 65?', a: 'Yes → proceed. No → skip unless you have an off-screener reason. Score is post-multiplier, so a low number could be raw weakness OR an environmental haircut — check the breakdown.' },
  { n: 3, q: 'Do I agree with the setup_type?',     a: 'Read the drivers in the expanded row. Disagree → skip; the geometry only works if the thesis matches.' },
  { n: 4, q: 'Is the stop tolerable in real $?',    a: '1.5 × ATR or recent swing low. If the dollar risk exceeds your per-trade budget, halve size or skip.' },
  { n: 5, q: 'Earnings nearby? CHASING?',           a: 'Earnings ≤ 14d → expect a haircut multiplier (visible in expanded breakdown). CHASING flag → entry is more than 3% past the structural trigger; either wait for a pullback or size down.' },
  { n: 6, q: 'Plan the exit before entry',          a: 'Stop hit → out, no negotiation. Target hit → out OR trail with 1× ATR. Max hold = upper hold-day band; trimmed automatically if it would span earnings.' },
]

export function SwingInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')
  const [topN, setTopN] = useState<number>(20)
  const [symbolsText, setSymbolsText] = useState<string>('')
  const [showLegend, setShowLegend] = useState<boolean>(false)
  const [expandedFactor, setExpandedFactor] = useState<string | null>(null)
  const [bypassGates, setBypassGates] = useState<boolean>(true)

  function parseSymbols(): string[] {
    return symbolsText
      .split(/[,\s]+/)
      .map(s => s.trim().toUpperCase())
      .filter(s => s.length > 0 && s.length <= 10)
      .slice(0, 20)
  }

  function handleScan() {
    onScan(topN, UNIVERSE_KEY)
  }

  function handleCustom() {
    const syms = parseSymbols()
    if (syms.length === 0) return
    onCustom(syms, bypassGates)
  }

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleCustom()
    }
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
      </div>

      {mode === 'scan' && (
        <div className="momentum-scan-row">
          <div className="momentum-scan-info">
            <span className="scan-desc">
              Scans <strong>{UNIVERSE_LABEL}</strong> — {UNIVERSE_HINT}
            </span>
            <span className="app-subtitle">
              Ranked by composite score (R:R 40 + setup 30 + context 20 + institutional 10).
              Hard gates: R:R ≥ 2.5, setup score ≥ 40. Top 3 receive AI commentary.
            </span>
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
            <button
              className="btn btn-primary"
              onClick={handleScan}
              disabled={loading}
            >
              {loading ? 'Scanning…' : '⚡ Scan Now'}
            </button>
          </div>
        </div>
      )}

      {mode === 'custom' && (
        <div className="momentum-scan-row" style={{ alignItems: 'stretch' }}>
          <div className="momentum-scan-info" style={{ flex: '0 0 auto', maxWidth: 320 }}>
            <span className="scan-desc">
              Custom symbols — max 20, comma- or space-separated.
            </span>
            <span className="app-subtitle">
              {bypassGates
                ? 'Strategy gates bypassed — all symbols with sufficient price history are returned.'
                : 'Hard gates active — same filters as auto-scan (price, ADV, R:R, setup score).'
              }
            </span>
          </div>
          <div
            className="momentum-scan-controls"
            style={{ flex: '1 1 auto', flexDirection: 'column', alignItems: 'stretch', gap: 8 }}
          >
            <textarea
              value={symbolsText}
              onChange={e => setSymbolsText(e.target.value)}
              onKeyDown={handleKey}
              placeholder="AAPL, MSFT, NVDA"
              rows={3}
              disabled={loading}
              style={{
                width: '100%',
                padding: 8,
                fontFamily: 'inherit',
                fontSize: 13,
                borderRadius: 4,
                border: '1px solid #334155',
                background: '#0f172a',
                color: 'inherit',
                resize: 'vertical',
              }}
            />
            <button
              className="btn btn-primary"
              onClick={handleCustom}
              disabled={loading || parseSymbols().length === 0}
            >
              {loading ? 'Scanning…' : `🚀 Run (${parseSymbols().length})`}
            </button>
            <button
              onClick={() => setBypassGates(v => !v)}
              disabled={loading}
              title={bypassGates
                ? 'Gates bypassed: click to enforce hard gates (price, ADV, R:R, setup score)'
                : 'Gates enforced: click to bypass hard gates and show all symbols'
              }
              style={{
                padding: '4px 10px',
                borderRadius: 4,
                border: `1px solid ${bypassGates ? '#4338ca' : '#334155'}`,
                background: bypassGates ? '#1e1b4b' : '#1e293b',
                color: bypassGates ? '#a5b4fc' : '#64748b',
                cursor: 'pointer',
                fontSize: 12,
                fontWeight: 600,
                transition: 'all 0.15s',
              }}
            >
              {bypassGates ? '🔓 Gates Off' : '🔒 Gates On'}
            </button>
          </div>
        </div>
      )}

      <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end' }}>
        <button
          className="link-btn"
          onClick={() => setShowLegend(v => !v)}
          title="How the Swing score is calculated"
          style={{
            background: 'transparent',
            border: 'none',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: 12,
            padding: '4px 6px',
          }}
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

          <div className="score-legend-factors">
            <div className="score-legend-header">Setup taxonomy — best_setup wins</div>
            {SETUP_GUIDE.map(s => (
              <div key={s.name} className="score-factor-block">
                <div className="score-factor-row">
                  <span className="score-factor-name" style={{ color: s.color, fontWeight: 700 }}>
                    {s.name}
                  </span>
                  <span className="score-factor-weight" style={{ color: '#94a3b8' }}>
                    hold {s.hold}
                  </span>
                  <span className="score-factor-detail">{s.signals}</span>
                </div>
                <div
                  style={{
                    margin: '4px 0 6px 28px',
                    fontSize: 11,
                    color: '#64748b',
                    fontStyle: 'italic',
                  }}
                >
                  {s.thesis}
                </div>
              </div>
            ))}
          </div>

          <div className="score-legend-factors">
            <div className="score-legend-header">
              Score breakdown — Composite = R:R 40 + Setup 30 + Context 20 + Institutional 10 (max 100)
            </div>
            {SCORE_BREAKDOWN.map(f => (
              f.weight === null ? (
                <div key={f.factor} className="score-factor-section">{f.factor}</div>
              ) : (
                <div key={f.factor} className="score-factor-block">
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
                      style={{
                        color:
                          f.weight >= 20 ? '#4ade80' :
                          f.weight >= 10 ? '#fbbf24' :
                          f.weight > 0   ? '#94a3b8' :
                                           '#64748b',
                      }}
                    >
                      {f.weight > 0 ? `+${f.weight} pts` : 'signal'}
                    </span>
                    <div className="score-factor-bar-wrap">
                      <div
                        className="score-factor-bar"
                        style={{
                          width: f.weight <= 0 ? '0%' : `${Math.min(f.weight / 40 * 100, 100)}%`,
                          background:
                            f.weight >= 20 ? '#4ade80' :
                            f.weight >= 10 ? '#fbbf24' : '#94a3b8',
                        }}
                      />
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
              )
            ))}
          </div>

          <div className="score-legend-factors">
            <div className="score-legend-header">Hard gates — applied before scoring</div>
            {GATES.map(g => (
              <div key={g.gate} className="score-factor-block">
                <div className="score-factor-row">
                  <span className="score-factor-name">{g.gate}</span>
                  <span className="score-factor-weight" style={{ color: '#f87171' }}>
                    {g.threshold}
                  </span>
                  <span className="score-factor-detail">{g.note}</span>
                </div>
              </div>
            ))}
          </div>

          <div className="decision-framework">
            <div className="decision-framework-header">Playbook — run top-down per row</div>
            <ol className="decision-steps">
              {PLAYBOOK.map(s => (
                <li key={s.n} className="decision-step">
                  <span className="decision-step-num">{s.n}</span>
                  <span className="decision-step-q">{s.q}</span>
                  <span className="decision-step-a">{s.a}</span>
                </li>
              ))}
            </ol>
            <div
              style={{
                marginTop: 8,
                padding: '6px 10px',
                background: '#0f172a',
                borderRadius: 5,
                fontSize: 11,
                color: '#64748b',
                borderLeft: '3px solid #334155',
              }}
            >
              <strong style={{ color: '#94a3b8' }}>Thumb rule</strong> — Risk is the
              decision; reward is the outcome. The stop must be the price at which the
              setup is structurally invalid, not a round-number comfort level.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

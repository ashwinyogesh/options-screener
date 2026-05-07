import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from '@tanstack/react-table'
import { useState, useMemo } from 'react'
import type { ReactElement } from 'react'
import type { CspResult, GroupedCspResult } from '../types/csp'
import type { InsightResult, InsightVerdict, StockCycle } from '../types/insight'
import { useInsight } from '../hooks/useInsight'

const col = createColumnHelper<GroupedCspResult>()

function fmt2(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}
function fmtAnn(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(1) + '%'
}
function fmtDelta(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(3)
}

function parseEnvDetail(detail: string): Record<string, number> {
  const out: Record<string, number> = {}
  for (const part of (detail ?? '').split(' ')) {
    const idx = part.indexOf(':')
    if (idx > 0) out[part.slice(0, idx)] = Number(part.slice(idx + 1))
  }
  return out
}
const ENV_MAX: Record<string, number> = { IVP: 35, Tr: 15, SMA: 5, SLP: 5, RSI: 20, OI: 20 }
const STRIKE_MAX: Record<string, number> = { 'Δ': 25, 'BA': 25, 'LQ': 15, 'ROC': 35 }
const DRAG_LABELS: Record<string, string> = {
  IVP: 'IV Percentile', Tr: 'Trend (52W)', SMA: 'SMA Alignment', SLP: 'SMA Slope', RSI: 'RSI', OI: 'Chain OI',
  'Δ': 'Delta', BA: 'Bid-Ask Spread', LQ: 'Liquidity', ROC: 'Ann. ROC',
}
function topDrags(envDetail: string, strikeDetail: string, n = 2) {
  const envPts = parseEnvDetail(envDetail)
  const strikePts = parseEnvDetail(strikeDetail)
  const all: { key: string; drag: number }[] = []
  for (const [k, max] of Object.entries(ENV_MAX)) {
    const v = envPts[k] ?? 0
    if (v >= 0) all.push({ key: k, drag: max - v })
  }
  for (const [k, max] of Object.entries(STRIKE_MAX)) {
    const v = strikePts[k] ?? 0
    if (v >= 0) all.push({ key: k, drag: max - v })
  }
  return all.sort((a, b) => b.drag - a.drag).slice(0, n)
}
function strikeSub(detail: string, key: string) {
  const pts = parseEnvDetail(detail)
  const v = pts[key], max = STRIKE_MAX[key]
  if (v == null || max == null) return null
  const ratio = v / max
  const color = ratio >= 0.70 ? '#4ade80' : ratio >= 0.45 ? '#fbbf24' : '#f87171'
  return <span style={{ fontSize: '10px', color, display: 'block', lineHeight: 1.2 }}>{Math.round(v)}/{max}</span>
}
function strikeColor(detail: string, key: string): string {
  const pts = parseEnvDetail(detail)
  const v = pts[key], max = STRIKE_MAX[key]
  if (v == null || max == null) return ''
  const ratio = v / max
  return ratio >= 0.70 ? '#4ade80' : ratio >= 0.45 ? '#fbbf24' : '#f87171'
}
function envSub(pts: Record<string, number>, key: string) {
  const v = pts[key], max = ENV_MAX[key]
  if (v == null || max == null) return null
  const ratio = v / max
  const color = ratio >= 0.70 ? '#4ade80' : ratio >= 0.45 ? '#fbbf24' : '#f87171'
  return <span style={{ fontSize: '10px', color, display: 'block', lineHeight: 1.2 }}>{Math.round(v)}/{max}</span>
}
function envSubInline(pts: Record<string, number>, key: string) {
  const v = pts[key], max = ENV_MAX[key]
  if (v == null || max == null) return null
  const ratio = v / max
  const color = ratio >= 0.70 ? '#4ade80' : ratio >= 0.45 ? '#fbbf24' : '#f87171'
  return <span style={{ fontSize: '10px', color, marginLeft: 3 }}>{Math.round(v)}/{max}</span>
}
function envColor(pts: Record<string, number>, key: string): string {
  const v = pts[key], max = ENV_MAX[key]
  if (v == null || max == null) return ''
  const ratio = v / max
  return ratio >= 0.70 ? '#4ade80' : ratio >= 0.45 ? '#fbbf24' : '#f87171'
}

// Ticker-level columns — for header rendering + sorting only.
// Cells are rendered manually in the tbody via rowSpan.
const COLUMNS = [
  col.accessor('symbol', { header: 'Symbol', cell: () => null, meta: { sticky: 1 } }),
  col.accessor('price', { header: 'Price', cell: () => null, meta: { sticky: 2 } }),
  col.accessor('bb_lower', {
    header: () => (
      <span className="col-tip" title="Bollinger Bands (20-period, 2σ) · Upper / Middle / Lower band around the closing price">
        BB Bands ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('sma_ratio', {
    header: () => (
      <span className="col-tip" title="SMA50 ÷ SMA200 · Ratio >1 means the 50-day average is above the 200-day average (bullish structure)  ·  diagnostic only in v3, not scored (Trend uses 52W).">
        SMA50/200 ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('dist_from_52w_high_pct', {
    header: () => (
      <span className="col-tip col-scored" title="Distance from the 52-week high · 0% = at the high · Negative = % below the high">
        52W Dist ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('iv_percentile', {
    header: () => (
      <span className="col-tip col-scored" title="IV Percentile — % of last 252 trading days where 30d HV was lower than today’s · v3.3 scored factor (35 pts, replaced IV/HV ratio) · ≥90th = full marks · HV-derived, never stale">
        IV Pct ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('rsi', {
    header: () => (
      <span className="col-tip col-scored" title="Relative Strength Index (14-period) · Momentum oscillator on a 0–100 scale · >70 overbought · <30 oversold">
        RSI(14) ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('vol_support_126_1', {
    header: () => (
      <span className="col-tip" title="Volume Profile support levels below the current price (126-day / 6M lookback) · High-volume price bins where buyers historically stepped in  ·  diagnostic only in v3, not scored (S/R distance dropped from scoring).">
        Vol Support 6M ⓘ
      </span>
    ),
    cell: () => null,
    enableSorting: false,
  }),
  col.accessor('earnings_date', { header: 'Earnings', cell: () => null }),
  // Hidden sort key — excluded from visible headers via columnVisibility
  col.accessor('best_score', { header: () => null, cell: () => null }),
]

function scorePatternTag(env: number, strike: number): ReactElement | null {
  if (env < 45 && strike < 45)
    return <span className="score-tag score-tag-both-weak" title="Both ENV and Strike are weak — structural drag on both sides">✗ Both weak</span>
  if (strike - env > 25)
    return <span className="score-tag score-tag-env-weak" title="Strike mechanics are strong but the stock environment is stressed — understand why ENV is low before entering">⚠ ENV weak</span>
  if (env - strike > 25)
    return <span className="score-tag score-tag-strike-weak" title="Stock environment looks good but the put mechanics are weak — poor premium, wide spread, or off-delta">⚠ Strike weak</span>
  return null
}

// ---------------------------------------------------------------------------
// Regime / VIX matrix helpers
// ---------------------------------------------------------------------------

type VixRegime = 'Calm' | 'Normal' | 'Elevated' | 'Panic'

/** Parse "$40\u201365" \u2192 { low: 40, high: 65 } or "$80+" \u2192 { low: 80, high: null } */
function parseBand(band: string): { low: number; high: number | null } {
  const cleaned = band.replace(/[$,]/g, '')
  const dash = cleaned.indexOf('\u2013')
  if (dash !== -1) {
    const lo = parseFloat(cleaned.slice(0, dash))
    const hi = parseFloat(cleaned.slice(dash + 1))
    return { low: isNaN(lo) ? 0 : lo, high: isNaN(hi) ? null : hi }
  }
  const plus = cleaned.indexOf('+')
  if (plus !== -1) {
    const lo = parseFloat(cleaned.slice(0, plus))
    return { low: isNaN(lo) ? 0 : lo, high: null }
  }
  const val = parseFloat(cleaned)
  return { low: isNaN(val) ? 0 : val, high: null }
}

/** VIX multiplier table: [row=cycle][col=vix] \u2192 percentage adjustment (+/- as decimal) */
const VIX_MULTIPLIER: Record<StockCycle, Record<VixRegime, number>> = {
  Bear:   { Calm: -0.10, Normal:  0.00, Elevated:  0.05, Panic: -0.15 },
  Normal: { Calm: -0.05, Normal:  0.00, Elevated:  0.10, Panic: -0.10 },
  Bull:   { Calm:  0.00, Normal:  0.00, Elevated:  0.15, Panic: -0.20 },
}

function applyVixMultiplier(band: string, cycle: StockCycle, vix: VixRegime): string {
  const { low, high } = parseBand(band)
  const mult = VIX_MULTIPLIER[cycle]?.[vix] ?? 0
  const adjLow = Math.round(low * (1 + mult))
  if (high === null) return `$${adjLow}+`
  const adjHigh = Math.round(high * (1 + mult))
  return `$${adjLow}\u2013$${adjHigh}`
}

/** Returns a human-readable multiplier string e.g. "+10%" or "0%" or "-5%" */
function formatMult(cycle: StockCycle, vix: VixRegime): string {
  const m = VIX_MULTIPLIER[cycle]?.[vix] ?? 0
  if (m === 0) return '0%'
  return (m > 0 ? '+' : '') + (m * 100).toFixed(0) + '%'
}

const VIX_REGIMES: VixRegime[] = ['Calm', 'Normal', 'Elevated', 'Panic']
const VIX_LABELS: Record<VixRegime, string> = {
  Calm: 'Calm (<15)',
  Normal: 'Normal (15\u201325)',
  Elevated: 'Elevated (25\u201335)',
  Panic: 'Panic (>35)',
}
const CYCLE_ROWS: StockCycle[] = ['Bear', 'Normal', 'Bull']

// ---------------------------------------------------------------------------
// InsightPanel
// ---------------------------------------------------------------------------

const VERDICT_STYLE: Record<InsightVerdict, { cls: string; label: string }> = {
  ENTER: { cls: 'insight-verdict-enter', label: 'ENTER' },
  WAIT:  { cls: 'insight-verdict-wait',  label: 'WAIT'  },
  SKIP:  { cls: 'insight-verdict-skip',  label: 'SKIP'  },
}

function InsightPanel({ insight, vixRegime }: { insight: InsightResult; vixRegime: VixRegime }) {
  const [showReasoning, setShowReasoning] = useState(false)
  const vs = VERDICT_STYLE[insight.verdict as InsightVerdict] ?? VERDICT_STYLE.WAIT
  const baseBands: Record<StockCycle, string> = {
    Bear:   insight.bear_band,
    Normal: insight.normal_band,
    Bull:   insight.bull_band,
  }
  return (
    <div className="insight-panel">
      {/* Header row */}
      <div className="insight-header">
        <span className={`insight-verdict ${vs.cls}`}>{vs.label}</span>
        <span className="insight-confidence">{Math.round(insight.confidence * 100)}% confidence</span>
        <span className="insight-regime-drivers">{insight.regime_drivers}</span>
        <span className="insight-current-regime">{insight.current_regime}</span>
      </div>

      {/* VIX context line */}
      <div className="insight-vix-line">
        VIX regime: <strong>{vixRegime}</strong>
        &nbsp;&middot;&nbsp;Stock cycle: <strong>{insight.stock_cycle}</strong>
      </div>

      {/* Base bands line */}
      <div className="insight-base-bands">
        <span className="insight-base-bands-label">LLM base </span>
        {CYCLE_ROWS.map((c, i) => (
          <span key={c} className={`insight-cycle-${c.toLowerCase()}`}>
            {i > 0 && <span className="insight-base-bands-sep">·</span>}
            {c} {baseBands[c]}
          </span>
        ))}
        <span className="insight-base-bands-hint"> (VIX Normal baseline)</span>
      </div>

      {/* Band matrix */}
      <div className="insight-matrix-wrapper">
        <table className="insight-matrix">
          <thead>
            <tr>
              <th></th>
              {VIX_REGIMES.map(v => (
                <th
                  key={v}
                  className={v === vixRegime ? 'insight-matrix-col-active' : ''}
                >
                  {VIX_LABELS[v]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CYCLE_ROWS.map(cycle => (
              <tr key={cycle}>
                <td className={`insight-matrix-row-label insight-cycle-${cycle.toLowerCase()}${cycle === insight.stock_cycle ? ' insight-matrix-row-active' : ''}`}>
                  {cycle}
                </td>
                {VIX_REGIMES.map(v => {
                  const isActive = cycle === insight.stock_cycle && v === vixRegime
                  const bandStr = applyVixMultiplier(baseBands[cycle], cycle, v)
                  return (
                    <td
                      key={v}
                      className={`insight-matrix-cell${isActive ? ' insight-matrix-cell-active' : ''}`}
                    >
                      {bandStr}
                      <span className="insight-matrix-mult">{formatMult(cycle, v)}</span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Ownership case + key risk */}
      <div className="insight-flags">
        <div className="insight-flag">
          <span className="insight-flag-label">Ownership</span>{insight.ownership_case}
        </div>
        <div className="insight-flag insight-flag-risk">
          <span className="insight-flag-label">Key risk</span>{insight.key_risk}
        </div>
      </div>

      {/* Summary paragraph */}
      <div className="insight-summary">{insight.summary}</div>

      {/* Reasoning toggle */}
      {insight.reasoning && (
        <div>
          <button
            className="insight-reasoning-toggle"
            onClick={() => setShowReasoning(v => !v)}
          >
            {showReasoning ? '▼ Hide reasoning' : '▶ Show reasoning'}
          </button>
          {showReasoning && (
            <div className="insight-reasoning">{insight.reasoning}</div>
          )}
        </div>
      )}

      {/* Disclaimer */}
      <div className="insight-disclaimer">* Fundamental valuation estimates — not investment advice</div>
    </div>
  )
}

function groupResults(results: CspResult[]): GroupedCspResult[] {
  const map = new Map<string, GroupedCspResult>()
  for (const r of results) {
    if (!map.has(r.symbol)) {
      map.set(r.symbol, {
        symbol: r.symbol,
        price: r.price,
        bb_upper: r.bb_upper,
        bb_middle: r.bb_middle,
        bb_lower: r.bb_lower,
        sma_ratio: r.sma_ratio,
        rsi: r.rsi,
        iv_rank: r.iv_rank,
        iv_percentile: r.iv_percentile,
        earnings_date: r.earnings_date,
        earnings_within_dte: false,
        vol_support_126_1: r.vol_support_126_1,
        vol_support_126_2: r.vol_support_126_2,
        vol_support_126_3: r.vol_support_126_3,
        dist_from_52w_high_pct: r.dist_from_52w_high_pct,
        iv_hv_ratio: null,
        env_detail: '',
        best_score: 0,
        using_hv_fallback: false,
        expirations: [],
      })
    }
    const group = map.get(r.symbol)!
    if (r.earnings_within_dte) group.earnings_within_dte = true
    if (r.using_hv_fallback) group.using_hv_fallback = true
    group.expirations.push({
      dte: r.dte,
      expiration: r.expiration,
      earnings_within_dte: r.earnings_within_dte,
      strikes: r.strikes,
      best_score: r.best_csp_score,
      using_hv_fallback: r.using_hv_fallback,
      expected_move: r.expected_move,
      chain_median_oi: r.chain_median_oi,
    })
  }
  for (const g of map.values()) {
    g.expirations.sort((a, b) => a.dte - b.dte)
    g.best_score = Math.max(...g.expirations.map(e => e.best_score))
    const bs = g.expirations.flatMap(e => e.strikes).find(s => s.is_best) ?? g.expirations[0]?.strikes[0]
    g.iv_hv_ratio = bs?.iv_hv_ratio ?? null
    g.env_detail = bs?.env_detail ?? ''
  }
  return [...map.values()].sort((a, b) => b.best_score - a.best_score)
}

interface Props {
  data: CspResult[]
}

export function CspTable({ data }: Props) {
  const groupedData = useMemo(() => groupResults(data), [data])
  const [sorting, setSorting] = useState<SortingState>([{ id: 'best_score', desc: true }])
  const [strikeExpanded, setStrikeExpanded] = useState<Set<string>>(new Set())
  const [insightExpanded, setInsightExpanded] = useState<Set<string>>(new Set())
  const [staleDismissed, setStaleDismissed] = useState(false)
  const { insights, loading: insightLoading, errors: insightErrors, fetchInsight } = useInsight()

  const anyStale = groupedData.some(
    r => r.using_hv_fallback || r.expirations.some(e => e.strikes.some(s => s.iv_stale))
  )

  const toggleStrikes = (key: string) => {
    setStrikeExpanded(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }

  const table = useReactTable({
    data: groupedData,
    columns: COLUMNS,
    state: { sorting, columnVisibility: { best_score: false } },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  if (groupedData.length === 0) return null

  const scoreCol = table.getColumn('best_score')
  const scoreSorted = scoreCol?.getIsSorted()

  const fmtSpread = (v: number | null) => {
    if (v == null) return <span className="dim">—</span>
    const cls = v > 10 ? 'spread-wide' : v > 5 ? 'spread-ok' : 'spread-tight'
    return <span className={cls}>{v.toFixed(1)}%</span>
  }
  const scoreFmt = (env: number | undefined, strike: number | undefined, final: number | undefined, envDetail?: string, strikeDetail?: string, highlight = false) => {
    if (final == null || isNaN(final)) return <span className="dim">—</span>
    const rounded = Math.round(final)
    const cls =
      rounded >= 75 ? 'score-strong'
      : rounded >= 65 ? 'score-good'
      : rounded >= 55 ? 'score-caution'
      : rounded >= 45 ? 'score-warn'
      : 'score-bad'
    const tag = (env != null && strike != null) ? scorePatternTag(env, strike) : null
    return (
      <span
        className={cls}
        style={highlight ? { fontWeight: 800, fontSize: '15px' } : {}}
        title={`Env: ${env?.toFixed(0) ?? '—'}  ·  Strike: ${strike?.toFixed(0) ?? '—'}  ·  Final: ${final.toFixed(0)}`}
      >
        {final.toFixed(0)}
        {env != null && strike != null && (
          <span style={{ fontSize: '10px', opacity: 0.7, display: 'block' }}>
            E{env.toFixed(0)} S{strike.toFixed(0)}
          </span>
        )}
        {tag && <span style={{ display: 'block' }}>{tag}</span>}
      </span>
    )
  }

  return (
    <div className="table-wrapper">
      {anyStale && !staleDismissed && (
        <div className="stale-banner">
          <span>⚠ Market closed — options quotes are stale (bid/ask = 0). Delta is estimated from 30-day historical volatility instead of implied volatility. Treat delta values as approximate.</span>
          <button className="stale-dismiss" onClick={() => setStaleDismissed(true)}>✕</button>
        </div>
      )}
      <table className="screener-table">
        <thead>
          {table.getHeaderGroups().map(hg => (
            <tr key={hg.id}>
              {hg.headers.map(header => {
                const stickyIdx = (header.column.columnDef.meta as { sticky?: number } | undefined)?.sticky
                const classes = [
                  header.column.getCanSort() ? 'sortable' : '',
                  stickyIdx ? `sticky-col sticky-col-${stickyIdx}` : '',
                ].filter(Boolean).join(' ')
                return (
                  <th
                    key={header.id}
                    onClick={header.column.getToggleSortingHandler()}
                    className={classes}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {header.column.getIsSorted() === 'asc' && ' ↑'}
                    {header.column.getIsSorted() === 'desc' && ' ↓'}
                  </th>
                )
              })}
              <th>
                <span className="col-tip" title="Days to Expiration  ·  Number of calendar days remaining until the option expires  ·  Score uses expirations within your min–max DTE range">
                  DTE ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Expected Move = price × HV(30d) × √(DTE/365)  ·  1σ dollar range by expiry  ·  Floor = price − EM">
                  Exp. Move ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Strike price with OTM% and premium  ·  Best score highlighted  ·  ▼ N more reveals all strikes">
                  Strike ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Option mid-price: (Bid + Ask) / 2  ·  Falls back to last-traded price if bid/ask = 0 (market closed)  ·  Per contract = × 100 shares">
                  Premium ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Black-Scholes put delta  ·  Negative for puts  ·  Approximates probability of expiring in-the-money  ·  v3 ideal −0.225 with symmetric bell: |Δ−(−0.225)| ≤ 0.025 = 20 pts · ≤ 0.075 = 13 · ≤ 0.125 = 7 · outside gate = 0">
                  Delta ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="(Ask − Bid) / Mid × 100  ·  Lower = tighter market  ·  >10% = illiquid">
                  Spread% ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Open Interest (or Volume when market open) at this specific strike  ·  Higher = more liquid">
                  OI/Vol ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="(Premium / Strike) × (365 / DTE) × 100  ·  Annualized yield on the cash collateral required to sell the put  ·  Collateral = strike × 100">
                  Ann. Return ⓘ
                </span>
              </th>
              <th
                className="sortable"
                onClick={() => scoreCol?.toggleSorting(scoreSorted === 'asc')}
              >
                <span className="col-tip" title="Final Score = 0.4×Env + 0.6×Strike  ·  v3.3 lean 8-factor model&#10;&#10;ENV SCORE (100 pts)&#10;  IV Percentile   35 pts  ≥90th pct=full; HV-derived, regime-agnostic&#10;  Trend (52W)     15 pts  CSP: ≤5% below 52W high=full&#10;  SMA Alignment    5 pts  SMA50>SMA200&#10;  SMA Slope        5 pts  SMA50 10d momentum&#10;  RSI(14)         20 pts  CSP: 42–62=full&#10;  Chain Median OI 20 pts  log circuit-breaker&#10;  Earnings in DTE −15 pts  penalty&#10;&#10;STRIKE SCORE (100 pts)&#10;  Delta           25 pts  symmetric bell, ideal −0.225&#10;  Bid-Ask Spread  25 pts  ≤1%=full&#10;  OI / Volume     15 pts  per-strike circuit-breaker&#10;  Annualized ROC  35 pts  ≥12%=full&#10;&#10;Diagnostic-only (not scored): EM Buffer, %OTM from Spot.">
                  Score ⓘ
                </span>
                {scoreSorted === 'asc' && ' ↑'}
                {scoreSorted === 'desc' && ' ↓'}
              </th>
              <th>
                <span className="col-tip" title="Top 2 factors with the largest point gap from their maximum · (max − actual)">Drags ⓘ</span>
              </th>
            </tr>
          ))}
        </thead>

        {table.getRowModel().rows.map(row => {
          const r = row.original

          // Pre-compute total <tr> count for rowSpan (main rows + expanded alt rows + insight rows)
          const totalRows = r.expirations.reduce((sum, exp) => {
            const expKey = `${r.symbol}-${exp.expiration}`
            const altCount = strikeExpanded.has(expKey)
              ? exp.strikes.filter(s => !s.is_best).length
              : 0
            const insightCount = insightExpanded.has(expKey) ? 1 : 0
            return sum + 1 + altCount + insightCount
          }, 0)

          const rows: ReactElement[] = []
          let absRowIdx = 0

          for (const [expIdx, exp] of r.expirations.entries()) {
            const key = `${r.symbol}-${exp.expiration}`
            const showAlts = strikeExpanded.has(key)
            if (!exp.strikes?.length) continue
            const bestStrike = exp.strikes.find(s => s.is_best) ?? exp.strikes[0]
            const altStrikes = exp.strikes.filter(s => !s.is_best)
            const dteCellRows = 1 + (showAlts ? altStrikes.length : 0)
            const isFirstRow = absRowIdx === 0
            const envPts = isFirstRow ? parseEnvDetail(r.env_detail) : {}
            rows.push(
              <tr key={`${expIdx}-best`} className={isFirstRow ? 'first-exp-row' : 'sub-exp-row'}>

                {/* Ticker-level cells — only on absolute first row, spanning all rows */}
                {isFirstRow && <>
                  <td rowSpan={totalRows} className="ticker-cell sticky-col sticky-col-1">
                    <strong>{r.symbol}</strong>
                  </td>
                  <td rowSpan={totalRows} className="sticky-col sticky-col-2">{fmt2(r.price)}</td>
                  <td rowSpan={totalRows}>
                    <span className="bb-bands">
                      <span className="bb-upper">{fmt2(r.bb_upper)}</span>
                      <span className="bb-middle">{fmt2(r.bb_middle)}</span>
                      <span className="bb-lower">{fmt2(r.bb_lower)}</span>
                    </span>
                  </td>
                  <td rowSpan={totalRows}>
                    {r.sma_ratio == null || isNaN(r.sma_ratio)
                      ? <span className="dim">—</span>
                      : <><span style={{ color: envColor(envPts, 'SMA') }}>{r.sma_ratio.toFixed(4)}</span><br />{envSub(envPts, 'SMA')}{envSub(envPts, 'SLP')}</>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {isNaN(r.dist_from_52w_high_pct)
                      ? <span className="dim">—</span>
                      : <><span style={{ color: envColor(envPts, 'Tr') }}>
                          {r.dist_from_52w_high_pct.toFixed(1)}%
                        </span><br />{envSub(envPts, 'Tr')}</>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.iv_percentile == null || isNaN(r.iv_percentile)
                      ? <span className="dim">—</span>
                      : <><span style={{ color: envColor(envPts, 'IVP') }}>
                          {r.iv_percentile.toFixed(0)}th
                        </span><br />{envSub(envPts, 'IVP')}</>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.rsi == null || isNaN(r.rsi)
                      ? <span className="dim">—</span>
                      : <><span style={{ color: envColor(envPts, 'RSI') }}>{r.rsi.toFixed(1)}</span><br />{envSub(envPts, 'RSI')}</>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {(() => {
                      const levels = [r.vol_support_126_1, r.vol_support_126_2, r.vol_support_126_3]
                        .filter((v): v is number => v != null)
                      if (levels.length === 0) return <span className="dim">—</span>
                      return (
                        <span className="vol-support">
                          {levels.map((lvl, i) => (
                            <span key={i} className="vol-support-level">
                              {fmt2(lvl)}
                              <span className="vol-support-pct"> {((lvl - r.price) / r.price * 100).toFixed(1)}%</span>
                            </span>
                          ))}
                        </span>
                      )
                    })()}
                  </td>
                  <td rowSpan={totalRows}>
                    {r.earnings_date
                      ? <span className={r.earnings_within_dte ? 'earnings-warn' : ''}>{r.earnings_date}{r.earnings_within_dte && ' ⚠'}</span>
                      : <span className="dim">—</span>
                    }
                  </td>
                </>}

                {/* DTE cell — spans main + alt strike rows for this expiration */}
                <td className="dte-cell" rowSpan={dteCellRows}>
                  <span className="dte-num">{exp.dte}</span><br />
                  <span className="expiry-date">{exp.expiration}</span>
                  {exp.earnings_within_dte && <span className="earnings-warn"> ⚠</span>}
                  <div className="oi-badge">OI: {exp.chain_median_oi > 0 ? (exp.chain_median_oi >= 1000 ? (exp.chain_median_oi / 1000).toFixed(1) + 'k' : Math.round(exp.chain_median_oi)) : <span className="dim">—</span>}{envSubInline(parseEnvDetail(bestStrike.env_detail), 'OI')}</div>
                  <div className="oi-badge">DTE☆{envSubInline(parseEnvDetail(bestStrike.env_detail), 'DTE')}</div>
                </td>
                {/* Expected Move cell — same rowSpan as DTE */}
                <td className="em-cell" rowSpan={dteCellRows}>
                  {exp.expected_move > 0
                    ? <>
                        <span className="em-range">±${exp.expected_move.toFixed(2)}</span><br />
                        <span className="em-floor" title="Lower bound of 1σ expected range">↓ {(r.price - exp.expected_move).toFixed(2)}</span>
                      </>
                    : <span className="dim">—</span>
                  }
                </td>

                {/* Best strike */}
                <td className="strike-cell best-strike">
                  <span className="strike-price">{fmt2(bestStrike.strike)}</span>
                  <span className="strike-fall"> {((bestStrike.strike - r.price) / r.price * 100).toFixed(1)}%</span>
                  {strikeSub(bestStrike.strike_detail, 'OTM')}
                  {altStrikes.length > 0 && (
                    <button className="strike-toggle" onClick={() => toggleStrikes(key)}>
                      {showAlts ? '▲ hide' : `▼ ${altStrikes.length} more`}
                    </button>
                  )}
                </td>
                <td className="prem-cell">${bestStrike.premium.toFixed(2)}</td>
                <td>
                      <span style={{ color: strikeColor(bestStrike.strike_detail, 'Δ') }}>
                    {fmtDelta(bestStrike.delta)}
                  </span>
                  {bestStrike.iv_fallback && <span className="iv-fallback-tag" title="Delta estimated from historical volatility (HV) — market closed/stale quotes">~HV</span>}
                  {strikeSub(bestStrike.strike_detail, 'Δ')}
                </td>
                <td><span style={{ color: strikeColor(bestStrike.strike_detail, 'BA') }}>{fmtSpread(bestStrike.bid_ask_spread_pct)}</span>{strikeSub(bestStrike.strike_detail, 'BA')}</td>
                <td>
                  <span style={{ color: strikeColor(bestStrike.strike_detail, 'LQ') }}>{bestStrike.lq_count >= 1000 ? (bestStrike.lq_count / 1000).toFixed(1) + 'k' : bestStrike.lq_count}</span>{strikeSub(bestStrike.strike_detail, 'LQ')}
                </td>
                <td>
                  {fmtAnn(bestStrike.annualized_return)}
                  {bestStrike.roc_annualized != null && (
                    <><br /><span style={{ fontSize: '10px', opacity: 0.85 }} title="Annualized ROC = (credit / (strike − credit)) × (365/DTE) × 100 — yield against capital actually tied up">ROC {bestStrike.roc_annualized.toFixed(1)}%</span></>
                  )}
                  {strikeSub(bestStrike.strike_detail, 'ROC')}
                </td>
                <td>
                  {scoreFmt(bestStrike.env_score, bestStrike.strike_score, bestStrike.csp_score, bestStrike.env_detail, bestStrike.strike_detail, true)}
                  <button
                    className="insight-btn"
                    title="Get AI insight for this trade"
                    onClick={() => {
                      const isOpen = insightExpanded.has(key)
                      setInsightExpanded(prev => {
                        const next = new Set(prev)
                        if (isOpen) next.delete(key); else next.add(key)
                        return next
                      })
                      if (!isOpen && !insights.has(key) && !insightLoading.has(key)) {
                        fetchInsight(key, {
                          symbol: r.symbol,
                          price: r.price,
                          strike: bestStrike.strike,
                          premium: bestStrike.premium,
                          dte: exp.dte,
                          expiration: exp.expiration,
                          earnings_within_dte: exp.earnings_within_dte,
                          env_score: bestStrike.env_score,
                          strike_score: bestStrike.strike_score,
                          final_score: bestStrike.csp_score,
                          env_detail: bestStrike.env_detail,
                          strike_detail: bestStrike.strike_detail,
                          roc_annualized: bestStrike.roc_annualized ?? null,
                          rsi: r.rsi,
                          iv_hv_ratio: bestStrike.iv_hv_ratio ?? null,
                          iv_percentile: r.iv_percentile ?? null,
                          dist_from_52w_high_pct: r.dist_from_52w_high_pct,
                        })
                      }
                    }}
                  >
                    {insightExpanded.has(key) ? '▲ AI' : '✦ AI'}
                  </button>
                </td>
                <td>
                  {topDrags(bestStrike.env_detail ?? '', bestStrike.strike_detail ?? '').map(d => (
                    <span key={d.key} style={{ display: 'block', fontSize: '12px', color: d.drag >= 15 ? '#f87171' : '#fb923c' }}>
                      {DRAG_LABELS[d.key] ?? d.key} −{Math.round(d.drag)}
                    </span>
                  ))}
                </td>
              </tr>
            )
            absRowIdx++

            // ── Alt strike rows (collapsed by default) ────────────────────
            if (showAlts) {
              for (const [si, s] of altStrikes.entries()) {
                rows.push(
                  <tr key={`${expIdx}-alt-${si}`} className="alt-strike-row">
                    <td className="strike-cell">
                      <span className="strike-price">{fmt2(s.strike)}</span>
                      <span className="strike-fall"> {((s.strike - r.price) / r.price * 100).toFixed(1)}%</span>
                      {strikeSub(s.strike_detail, 'OTM')}
                    </td>
                    <td className="prem-cell">${s.premium.toFixed(2)}</td>
                    <td>
                      <span style={{ color: strikeColor(s.strike_detail, 'Δ') }}>
                        {fmtDelta(s.delta)}
                      </span>
                      {s.iv_fallback && <span className="iv-fallback-tag" title="Delta estimated from historical volatility (HV) — market closed/stale quotes">~HV</span>}
                      {strikeSub(s.strike_detail, 'Δ')}
                    </td>
                    <td><span style={{ color: strikeColor(s.strike_detail, 'BA') }}>{fmtSpread(s.bid_ask_spread_pct)}</span>{strikeSub(s.strike_detail, 'BA')}</td>
                    <td>
                      <span style={{ color: strikeColor(s.strike_detail, 'LQ') }}>{s.lq_count >= 1000 ? (s.lq_count / 1000).toFixed(1) + 'k' : s.lq_count}</span>{strikeSub(s.strike_detail, 'LQ')}
                    </td>
                    <td>
                      {fmtAnn(s.annualized_return)}
                      {s.roc_annualized != null && (
                        <><br /><span style={{ fontSize: '10px', opacity: 0.85 }}>ROC {s.roc_annualized.toFixed(1)}%</span></>
                      )}
                      {strikeSub(s.strike_detail, 'ROC')}
                    </td>
                    <td>{scoreFmt(s.env_score, s.strike_score, s.csp_score, s.env_detail, s.strike_detail)}</td>
                    <td>
                      {topDrags(s.env_detail ?? '', s.strike_detail ?? '').map(d => (
                        <span key={d.key} style={{ display: 'block', fontSize: '12px', color: d.drag >= 15 ? '#f87171' : '#fb923c' }}>
                          {DRAG_LABELS[d.key] ?? d.key} −{Math.round(d.drag)}
                        </span>
                      ))}
                    </td>
                  </tr>
                )
                absRowIdx++
              }
            }

            // ── AI insight row (full-colspan, after all strike rows) ───────
            if (insightExpanded.has(key)) {
              rows.push(
                <tr key={`${expIdx}-insight`} className="insight-row">
                  <td colSpan={19}>
                    {insightLoading.has(key) ? (
                      <div className="insight-loading">✦ Fetching AI insight…</div>
                    ) : insightErrors.get(key) ? (
                      <div className="insight-error">⚠ {insightErrors.get(key)}</div>
                    ) : insights.get(key) ? (
                      <InsightPanel insight={insights.get(key)!} vixRegime={(insights.get(key)!.vix_regime as VixRegime) ?? 'Normal'} />
                    ) : null}
                  </td>
                </tr>
              )
              absRowIdx++
            }
          }

          return (
            <tbody
              key={r.symbol}
              className={`ticker-group${r.earnings_within_dte ? ' group-earnings-warn' : ''}`}
            >
              {rows}
            </tbody>
          )
        })}
      </table>
      <p className="table-note">
        HV Rank = 30-day historical volatility ranked over a 252-day window (used as IV proxy). Best strike highlighted by highest CSP score.
      </p>
    </div>
  )
}

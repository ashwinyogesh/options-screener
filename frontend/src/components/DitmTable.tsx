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
import type { DitmResult, GroupedDitmResult } from '../types/ditm'

const col = createColumnHelper<GroupedDitmResult>()

function fmt2(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}
function fmtDelta(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(3)
}
function fmtPct(n: number | null | undefined, decimals = 1): string {
  if (n == null) return '—'
  return n.toFixed(decimals) + '%'
}

// ---------------------------------------------------------------------------
// Detail string parsing (mirrors CspTable pattern)
// ---------------------------------------------------------------------------

function parseDetail(detail: string): Record<string, number> {
  const out: Record<string, number> = {}
  for (const part of (detail ?? '').split(' ')) {
    const idx = part.indexOf(':')
    if (idx > 0) out[part.slice(0, idx)] = Number(part.slice(idx + 1))
  }
  return out
}

// v3 ENV factor keys + max pts (ADR-0008)
const ENV_MAX: Record<string, number> = {
  Tr: 25, Ret: 15, R2: 10, '52W': 20, WRSI: 15, LQ: 15,
}
// v3 Strike factor keys + max pts
const STRIKE_MAX: Record<string, number> = {
  'Δ': 20, Lev: 25, Ext: 25, BA: 20, IV: 10,
}
const DRAG_LABELS: Record<string, string> = {
  Tr: 'Trend', WRSI: 'Weekly RSI', '52W': '52W Dist',
  Ret: '200d Return', R2: 'Trend Stability R²', LQ: 'Liquidity',
  'Δ': 'Delta', Lev: 'Leverage', Ext: 'Extrinsic%',
  BA: 'Bid-Ask Spread', IV: 'IV Pctile',
}
function topDrags(envDetail: string, strikeDetail: string, n = 2) {
  const envPts = parseDetail(envDetail)
  const strikePts = parseDetail(strikeDetail)
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

function subScore(pts: Record<string, number>, key: string, maxMap: Record<string, number>) {
  const v = pts[key], max = maxMap[key]
  if (v == null || max == null) return null
  const ratio = v / max
  const color = ratio >= 0.70 ? '#4ade80' : ratio >= 0.45 ? '#fbbf24' : '#f87171'
  return <span style={{ fontSize: '10px', color, display: 'block', lineHeight: 1.2 }}>{Math.round(v)}/{max}</span>
}

function subColor(pts: Record<string, number>, key: string, maxMap: Record<string, number>): string {
  const v = pts[key], max = maxMap[key]
  if (v == null || max == null) return ''
  const ratio = v / max
  return ratio >= 0.70 ? '#4ade80' : ratio >= 0.45 ? '#fbbf24' : '#f87171'
}

function subInline(pts: Record<string, number>, key: string, maxMap: Record<string, number>) {
  const v = pts[key], max = maxMap[key]
  if (v == null || max == null) return null
  const ratio = v / max
  const color = ratio >= 0.70 ? '#4ade80' : ratio >= 0.45 ? '#fbbf24' : '#f87171'
  return <span style={{ fontSize: '10px', color, marginLeft: 3 }}>{Math.round(v)}/{max}</span>
}

// ---------------------------------------------------------------------------
// Score colour (same 5-tier thresholds as CSP/CC)
// ---------------------------------------------------------------------------

function scoreFmt(
  env: number | undefined,
  strike: number | undefined,
  final: number | undefined,
  highlight = false,
) {
  if (final == null || isNaN(final)) return <span className="dim">—</span>
  const rounded = Math.round(final)
  const cls =
    rounded >= 75 ? 'score-strong'
    : rounded >= 65 ? 'score-good'
    : rounded >= 55 ? 'score-caution'
    : rounded >= 45 ? 'score-warn'
    : 'score-bad'
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
    </span>
  )
}

const fmtSpread = (v: number | null) => {
  if (v == null) return <span className="dim">—</span>
  const cls = v > 10 ? 'spread-wide' : v > 5 ? 'spread-ok' : 'spread-tight'
  return <span className={cls}>{v.toFixed(1)}%</span>
}

// ---------------------------------------------------------------------------
// Grouping
// ---------------------------------------------------------------------------

// Ticker-level columns (header rendering / sorting only; cells rendered via rowSpan)
const COLUMNS = [
  col.accessor('symbol',                 { header: 'Symbol',   cell: () => null, meta: { sticky: 1 } }),
  col.accessor('price',                  { header: 'Price',    cell: () => null, meta: { sticky: 2 } }),
  col.accessor('sma_ratio',              { header: () => <span className="col-tip col-scored" title="SMA50 ÷ SMA200 · >1 = SMA50 above SMA200 (uptrend)">Trend ⓘ</span>, cell: () => null }),
  col.accessor('weekly_rsi',             { header: () => <span className="col-tip col-scored" title="Weekly RSI(14) — medium-term momentum on weekly closes · 50–65 = ideal">W-RSI ⓘ</span>, cell: () => null }),
  col.accessor('ret_200d',               { header: () => <span className="col-tip col-scored" title="200-day median-anchored return · close_today / median(closes 200d ago) − 1">200d Ret ⓘ</span>, cell: () => null }),
  col.accessor('trend_r2',               { header: () => <span className="col-tip col-scored" title="R² of 50-day OLS price regression · measures trend smoothness · ≥0.85=10pts · 0.70–0.85→7.5–10 · <0.30=0 (v3.2)">R² ⓘ</span>, cell: () => null }),
  col.accessor('dist_from_52w_high_pct', { header: () => <span className="col-tip col-scored" title="Distance from 52-week high · 0% = at the high · negative = % below">52W Dist ⓘ</span>, cell: () => null }),
  col.accessor('earnings_date',          { header: 'Earnings', cell: () => null }),
  col.accessor('best_score',             { header: () => null, cell: () => null }),
]

function groupResults(results: DitmResult[]): GroupedDitmResult[] {
  const map = new Map<string, GroupedDitmResult>()
  for (const r of results) {
    if (!map.has(r.symbol)) {
      map.set(r.symbol, {
        symbol: r.symbol,
        price: r.price,
        sma_ratio: r.sma_ratio,
        hv_rank: r.hv_rank,
        hv30: r.hv30,
        weekly_rsi: r.weekly_rsi,
        ret_200d: r.ret_200d,
        dist_from_52w_high_pct: r.dist_from_52w_high_pct,
        earnings_date: r.earnings_date,
        days_to_earnings: r.days_to_earnings,
        earnings_within_dte: false,
        gap_3d_pct: r.gap_3d_pct,
        macro_hold: false,
        best_score: 0,
        expirations: [],
        env_detail: '',
        iv_percentile: r.iv_percentile,
        trend_r2: r.trend_r2,
      })
    }
    const g = map.get(r.symbol)!
    if (r.earnings_within_dte) g.earnings_within_dte = true
    if (r.macro_hold) g.macro_hold = true
    g.expirations.push({
      dte: r.dte,
      expiration: r.expiration,
      earnings_within_dte: r.earnings_within_dte,
      strikes: r.strikes,
      best_score: r.best_ditm_score,
      macro_hold: r.macro_hold,
      chain_median_oi: r.chain_median_oi,
    })
  }
  for (const g of map.values()) {
    g.expirations.sort((a, b) => a.dte - b.dte)
    g.best_score = Math.max(...g.expirations.map(e => e.best_score))
    const bestStrike = g.expirations.flatMap(e => e.strikes).find(s => s.is_best)
      ?? g.expirations[0]?.strikes[0]
    g.env_detail = bestStrike?.env_detail ?? ''
  }
  return [...map.values()].sort((a, b) => b.best_score - a.best_score)
}

interface Props {
  data: DitmResult[]
  macroPass: boolean
  vixLevel: number | null
  vix5dChange: number | null
  spyAboveSma200: boolean
}

export function DitmTable({ data, macroPass, vixLevel, vix5dChange, spyAboveSma200 }: Props) {
  const groupedData = useMemo(() => groupResults(data), [data])
  const [sorting, setSorting] = useState<SortingState>([{ id: 'best_score', desc: true }])
  const [strikeExpanded, setStrikeExpanded] = useState<Set<string>>(new Set())

  const anyHvFallback = groupedData.some(r =>
    r.expirations.some(e => e.strikes.some(s => s.iv_fallback))
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

  return (
    <div className="table-wrapper">
      {/* Macro status — always visible */}
      {(() => {
        const vixHigh = vixLevel != null && vixLevel >= 25
        const vixRising = vix5dChange != null && vix5dChange > 0
        const vixFailing = vixHigh && vixRising
        const spyFailing = !spyAboveSma200
        const failReasons: string[] = []
        if (vixFailing) failReasons.push(`VIX ${vixLevel!.toFixed(1)} ≥ 25 and rising (+${vix5dChange!.toFixed(1)} over 5d)`)
        if (spyFailing) failReasons.push('SPY below SMA200')
        return (
          <div className="stale-banner" style={{ background: macroPass ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.10)', borderColor: macroPass ? '#22c55e' : '#ef4444' }}>
            <span style={{ color: macroPass ? '#4ade80' : '#f87171' }}>
              {macroPass ? '✓ Macro Pass' : '⚠ Macro Hold'}
            </span>
            <span style={{ color: '#94a3b8', marginLeft: 10, fontSize: '12px' }}>
              VIX {vixLevel != null ? vixLevel.toFixed(1) : '—'}
              {vix5dChange != null && (
                <span style={{ color: vix5dChange > 0 ? '#f87171' : '#4ade80', marginLeft: 4 }}>
                  ({vix5dChange > 0 ? '+' : ''}{vix5dChange.toFixed(1)} 5d)
                </span>
              )}
              {macroPass
                ? ' · SPY > SMA200 · Low macro risk'
                : ` · Failing: ${failReasons.join(', ')} — scores shown for reference`}
            </span>
          </div>
        )
      })()}
      {anyHvFallback && (
        <div className="stale-banner">
          <span>⚠ Some delta values are HV-estimated — bid/ask = 0 on those strikes (illiquid or pre-open quotes).</span>
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
              {/* Expiration-level header */}
              <th>
                <span className="col-tip" title="Days to Expiration · DITM uses longer DTE (90–365) for sustained directional exposure">
                  DTE ⓘ
                </span>
              </th>
              {/* Strike-level headers */}
              <th>
                <span className="col-tip" title="Strike price · ITM for calls (strike < price) · % shown is how far ITM">
                  Strike ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Option mid-price: (Bid + Ask) / 2 · Falls back to last-traded price if bid/ask = 0">
                  Mid ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Black-Scholes call delta · Sweet spot 0.80–0.85 · Approximates $ move per $1 stock move">
                  Δ ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Leverage = delta × price / mid · v3 NEW (audit #1) · The headline DITM metric: exposure-per-dollar deployed · Sweet spot 2.5–3.5×">
                  Lev ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Extrinsic value / strike × 100 · The time-value cost · DITM buyers want this BELOW 4%">
                  Extrinsic% ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="(Strike + Mid − Price) / Price × 100 · Stock must rise this much by expiry to break even · Display only">
                  BE% ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="(Ask − Bid) / Mid × 100 · Transaction cost on entry and exit">
                  Spread% ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="IV Percentile — % of last 252d where HV < today HV · v3 single vol-cheapness factor (10 pts) · Inverted: low = cheap = full credit">
                  IV%ile ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Per-strike OI · colored by Chain Liquidity factor (audit #12 fix — was incorrectly colored by IV)">
                  OI ⓘ
                </span>
              </th>
              <th
                className="sortable"
                onClick={() => scoreCol?.toggleSorting(scoreSorted === 'asc')}
              >
                <span className="col-tip" title="Final Score = (0.5×Env + 0.5×Strike) × macro_mult (0.85 if macro hold)&#10;&#10;ENV (100 pts)&#10;  Trend Strength       25 pts  P>SMA50>SMA200 (soft factor)&#10;  200d Return          15 pts  ≥25%=full (v3.2: compressed)&#10;  Trend Stability R²   10 pts  OLS R² of 50-day price (v3.2 NEW)&#10;  52W High Dist.       20 pts  peak 3–12% off highs (v3.2 tent curve)&#10;  Weekly RSI           15 pts  50–65=full&#10;  Chain Liquidity      15 pts  log10 ref 500&#10;  Earnings (DTE-scaled)  up to −15 pts penalty&#10;&#10;STRIKE (100 pts)&#10;  Delta            20 pts  0.82–0.90 sweet spot (v3.2)&#10;  Leverage         25 pts  δ×price/mid · flat 2.5–4.0× · hard 0 at ≥5× (v3.2)&#10;  Extrinsic%       25 pts  &lt;2%=full&#10;  Bid-Ask Spread   20 pts  ≤2%=full&#10;  IV Percentile    10 pts  ≤25th=full (inverted)">
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

          const totalRows = r.expirations.reduce((sum, exp) => {
            const altCount = strikeExpanded.has(`${r.symbol}-${exp.expiration}`)
              ? exp.strikes.filter(s => !s.is_best).length
              : 0
            return sum + 1 + altCount
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

            const envPts = isFirstRow ? parseDetail(r.env_detail) : {}

            // Trend label from SMA ratio
            const trendLabel = r.sma_ratio > 1.0
              ? (r.sma_ratio > 1.02 ? 'Strong' : 'Bullish')
              : 'Neutral'

            rows.push(
              <tr key={`${expIdx}-best`} className={isFirstRow ? 'first-exp-row' : 'sub-exp-row'}>

                {/* ── Ticker-level cells (first row only, rowSpan all) ── */}
                {isFirstRow && <>
                  <td rowSpan={totalRows} className="ticker-cell sticky-col sticky-col-1">
                    <strong>{r.symbol}</strong>
                    {r.gap_3d_pct >= 3 && (
                      <span className="earnings-warn" title={`Recent gap: ${r.gap_3d_pct.toFixed(1)}%`}> ⚠</span>
                    )}
                  </td>
                  <td rowSpan={totalRows} className="sticky-col sticky-col-2">{fmt2(r.price)}</td>

                  {/* Trend — v3: soft 25-pt factor, no longer a hard gate */}
                  <td rowSpan={totalRows}>
                    <span style={{ color: subColor(envPts, 'Tr', ENV_MAX) || '#94a3b8', fontWeight: 600 }}>{trendLabel}</span>
                    <br />
                    <span style={{ fontSize: '10px', color: '#94a3b8' }}>
                      {isNaN(r.sma_ratio) ? '—' : r.sma_ratio.toFixed(4)}
                    </span>
                    {subScore(envPts, 'Tr', ENV_MAX)}
                  </td>

                  {/* Weekly RSI */}
                  <td rowSpan={totalRows}>
                    {isNaN(r.weekly_rsi)
                      ? <span className="dim">—</span>
                      : <>
                          <span style={{ color: subColor(envPts, 'WRSI', ENV_MAX) }}>
                            {r.weekly_rsi.toFixed(1)}
                          </span>
                          {subScore(envPts, 'WRSI', ENV_MAX)}
                        </>
                    }
                  </td>

                  {/* 200d Return */}
                  <td rowSpan={totalRows}>
                    {isNaN(r.ret_200d)
                      ? <span className="dim">—</span>
                      : <>
                          <span style={{ color: subColor(envPts, 'Ret', ENV_MAX) }}>
                            {r.ret_200d >= 0 ? '+' : ''}{r.ret_200d.toFixed(1)}%
                          </span>
                          {subScore(envPts, 'Ret', ENV_MAX)}
                        </>
                    }
                  </td>

                  {/* Trend Stability R² — v3.2 */}
                  <td rowSpan={totalRows}>
                    {r.trend_r2 == null
                      ? <span className="dim">—</span>
                      : <>
                          <span style={{ color: subColor(envPts, 'R2', ENV_MAX) }}>
                            {r.trend_r2.toFixed(2)}
                          </span>
                          {subScore(envPts, 'R2', ENV_MAX)}
                        </>
                    }
                  </td>

                  {/* 52W Dist */}
                  <td rowSpan={totalRows}>
                    {isNaN(r.dist_from_52w_high_pct)
                      ? <span className="dim">—</span>
                      : <>
                          <span style={{ color: subColor(envPts, '52W', ENV_MAX) }}>
                            {r.dist_from_52w_high_pct.toFixed(1)}%
                          </span>
                          {subScore(envPts, '52W', ENV_MAX)}
                        </>
                    }
                  </td>

                  {/* Earnings — v3: DTE-scaled penalty (audit #9), no longer a hard gate */}
                  <td rowSpan={totalRows}>
                    {r.earnings_date
                      ? <>
                          <span className={r.days_to_earnings != null && r.days_to_earnings <= 7 ? 'earnings-warn' : ''}>
                            {r.earnings_date}
                          </span>
                          {(() => {
                            const dte = r.days_to_earnings
                            if (dte == null) return null
                            const tone =
                              dte <= 7 ? '#f87171'
                              : dte <= 14 ? '#fb923c'
                              : '#94a3b8'
                            const label =
                              dte <= 7 ? `−15 ENV × min(1, 30/dte)`
                              : dte <= 14 ? `−7 ENV × min(1, 30/dte)`
                              : `${dte}d`
                            return (
                              <span
                                style={{ fontSize: '10px', color: tone, display: 'block', lineHeight: 1.2 }}
                                title={`v3 DTE-scaled earnings penalty (replaces v2 hard gate)`}
                              >
                                {label}
                              </span>
                            )
                          })()}
                        </>
                      : <span className="dim">—</span>
                    }
                  </td>
                </>}

                {/* ── DTE cell (spans best + alt strikes for this expiry) ── */}
                <td className="dte-cell" rowSpan={dteCellRows}>
                  <span className="dte-num">{exp.dte}</span><br />
                  <span className="expiry-date">{exp.expiration}</span>
                  {exp.earnings_within_dte && <span className="earnings-warn"> ⚠</span>}
                  <div className="oi-badge">
                    OI: {exp.chain_median_oi > 0
                      ? (exp.chain_median_oi >= 1000
                        ? (exp.chain_median_oi / 1000).toFixed(1) + 'k'
                        : Math.round(exp.chain_median_oi))
                      : <span className="dim">—</span>}
                    {subInline(parseDetail(bestStrike.env_detail), 'LQ', ENV_MAX)}
                  </div>
                </td>

                {/* ── Best strike cells ── */}
                <td className="strike-cell best-strike">
                  <span className="strike-price">{fmt2(bestStrike.strike)}</span>
                  {/* ITM % (negative = below price) */}
                  <span className="strike-fall" style={{ color: '#4ade80' }}>
                    {' '}{((bestStrike.strike - r.price) / r.price * 100).toFixed(1)}%
                  </span>
                  {altStrikes.length > 0 && (
                    <button className="strike-toggle" onClick={() => toggleStrikes(key)}>
                      {showAlts ? '▲ hide' : `▼ ${altStrikes.length} more`}
                    </button>
                  )}
                </td>

                <td className="prem-cell">${bestStrike.mid.toFixed(2)}</td>

                <td>
                  <span style={{ color: subColor(parseDetail(bestStrike.strike_detail), 'Δ', STRIKE_MAX) }}>
                    +{fmtDelta(bestStrike.delta)}
                  </span>
                  {bestStrike.iv_fallback && (
                    <span className="iv-fallback-tag" title="Delta estimated from historical volatility — market closed/stale quotes">~HV</span>
                  )}
                  {subScore(parseDetail(bestStrike.strike_detail), 'Δ', STRIKE_MAX)}
                </td>

                {/* Leverage — v3 NEW (audit #1) */}
                <td>
                  {(() => {
                    const lev = bestStrike.mid > 0 ? bestStrike.delta * r.price / bestStrike.mid : 0
                    return <>
                      <span style={{ color: subColor(parseDetail(bestStrike.strike_detail), 'Lev', STRIKE_MAX) }}>
                        {lev.toFixed(2)}×
                      </span>
                      {subScore(parseDetail(bestStrike.strike_detail), 'Lev', STRIKE_MAX)}
                    </>
                  })()}
                </td>

                <td>
                  <span style={{ color: subColor(parseDetail(bestStrike.strike_detail), 'Ext', STRIKE_MAX) }}>
                    {fmtPct(bestStrike.extrinsic_pct)}
                  </span>
                  {subScore(parseDetail(bestStrike.strike_detail), 'Ext', STRIKE_MAX)}
                </td>

                <td>
                  <span className="dim">{fmtPct(bestStrike.breakeven_pct)}</span>
                </td>

                <td>
                  <span style={{ color: subColor(parseDetail(bestStrike.strike_detail), 'BA', STRIKE_MAX) }}>
                    {fmtSpread(bestStrike.bid_ask_spread_pct)}
                  </span>
                  {subScore(parseDetail(bestStrike.strike_detail), 'BA', STRIKE_MAX)}
                </td>

                {/* IV Percentile — v3: explicit column (was hidden under OI — audit #12) */}
                <td>
                  <span style={{ color: subColor(parseDetail(bestStrike.strike_detail), 'IV', STRIKE_MAX) }}>
                    {r.iv_percentile != null ? `${r.iv_percentile.toFixed(0)}` : '—'}
                  </span>
                  {subScore(parseDetail(bestStrike.strike_detail), 'IV', STRIKE_MAX)}
                </td>

                {/* OI — v3: colored by LQ (audit #12 fix) */}
                <td>
                  <span style={{ color: subColor(parseDetail(bestStrike.env_detail), 'LQ', ENV_MAX) }}>
                    {bestStrike.chain_oi >= 1000
                      ? (bestStrike.chain_oi / 1000).toFixed(1) + 'k'
                      : bestStrike.chain_oi}
                  </span>
                </td>

                <td>
                  {scoreFmt(bestStrike.env_score, bestStrike.strike_score, bestStrike.ditm_score, true)}
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

            {/* Alt strike rows */}
            if (showAlts) {
              for (const [si, s] of altStrikes.entries()) {
                rows.push(
                  <tr key={`${expIdx}-alt-${si}`} className="alt-strike-row">
                    <td className="strike-cell">
                      <span className="strike-price">{fmt2(s.strike)}</span>
                      <span className="strike-fall" style={{ color: '#4ade80' }}>
                        {' '}{((s.strike - r.price) / r.price * 100).toFixed(1)}%
                      </span>
                    </td>
                    <td className="prem-cell">${s.mid.toFixed(2)}</td>
                    <td>
                      <span style={{ color: subColor(parseDetail(s.strike_detail), 'Δ', STRIKE_MAX) }}>
                        +{fmtDelta(s.delta)}
                      </span>
                      {s.iv_fallback && (
                        <span className="iv-fallback-tag" title="Delta estimated from historical volatility">~HV</span>
                      )}
                      {subScore(parseDetail(s.strike_detail), 'Δ', STRIKE_MAX)}
                    </td>
                    {/* Leverage */}
                    <td>
                      {(() => {
                        const lev = s.mid > 0 ? s.delta * r.price / s.mid : 0
                        return <>
                          <span style={{ color: subColor(parseDetail(s.strike_detail), 'Lev', STRIKE_MAX) }}>
                            {lev.toFixed(2)}×
                          </span>
                          {subScore(parseDetail(s.strike_detail), 'Lev', STRIKE_MAX)}
                        </>
                      })()}
                    </td>
                    <td>
                      <span style={{ color: subColor(parseDetail(s.strike_detail), 'Ext', STRIKE_MAX) }}>
                        {fmtPct(s.extrinsic_pct)}
                      </span>
                      {subScore(parseDetail(s.strike_detail), 'Ext', STRIKE_MAX)}
                    </td>
                    <td>
                      <span className="dim">{fmtPct(s.breakeven_pct)}</span>
                    </td>
                    <td>
                      <span style={{ color: subColor(parseDetail(s.strike_detail), 'BA', STRIKE_MAX) }}>
                        {fmtSpread(s.bid_ask_spread_pct)}
                      </span>
                      {subScore(parseDetail(s.strike_detail), 'BA', STRIKE_MAX)}
                    </td>
                    {/* IV%ile — explicit column */}
                    <td>
                      <span style={{ color: subColor(parseDetail(s.strike_detail), 'IV', STRIKE_MAX) }}>
                        {r.iv_percentile != null ? `${r.iv_percentile.toFixed(0)}` : '—'}
                      </span>
                      {subScore(parseDetail(s.strike_detail), 'IV', STRIKE_MAX)}
                    </td>
                    {/* OI — colored by LQ */}
                    <td>
                      <span style={{ color: subColor(parseDetail(s.env_detail), 'LQ', ENV_MAX) }}>
                        {s.chain_oi >= 1000 ? (s.chain_oi / 1000).toFixed(1) + 'k' : s.chain_oi}
                      </span>
                    </td>
                    <td>
                      {scoreFmt(s.env_score, s.strike_score, s.ditm_score)}
                    </td>
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
          }

          return <tbody key={r.symbol}>{rows}</tbody>
        })}
      </table>
      <div className="table-footer-note">
        v3 (ADR-0008): Trend gate and HV Rank gate removed. Macro hold demotes scores by 15%.
        ⚠ in Symbol = overnight gap ≥ 3% in last 3 sessions. Best strike highlighted by highest DITM score.
      </div>
    </div>
  )
}

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
import type { DitmResult, DitmStrikeInfo, GroupedDitmResult } from '../types/ditm'

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
// v4 factor breakdown helpers (ADR-0032)
// ---------------------------------------------------------------------------

// Signed fractional weights per factor (must mirror backend ditm_v4.GROUP_WEIGHT_CAPS
// allocation). Used only to color/badge per-cell contributions.
const V4_FACTOR_WEIGHTS: Record<string, number> = {
  // Valuation (-)
  ps_ttm: -0.146, ev_sales: -0.129, ev_ebitda: -0.075,
  // Capital (+)
  debt_to_equity: 0.097, nd_ebitda: 0.053,
  // Technical
  wk_rsi: -0.076, dist52w: -0.043, hv30: -0.046, ret_200d: 0.034,
  // Macro (-)
  sector_rs_6m: -0.050,
  // Option chain
  leverage: 0.094, delta: 0.080, extrinsic_pct: -0.076,
}

const V4_FACTOR_LABELS: Record<string, string> = {
  ps_ttm: 'P/S', ev_sales: 'EV/S', ev_ebitda: 'EV/EBITDA',
  debt_to_equity: 'D/E', nd_ebitda: 'ND/EBITDA',
  wk_rsi: 'W-RSI', dist52w: '52W Dist', hv30: 'HV30', ret_200d: '200d Ret',
  sector_rs_6m: 'Sector RS',
  leverage: 'Leverage', delta: 'Delta', extrinsic_pct: 'Extrinsic%',
}

// Normalised "goodness" of a single factor's contribution in [0, 1]
// (1 = best possible contribution given the sign of the weight, 0 = worst).
function factorGoodness(contrib: number | undefined, weight: number): number {
  if (contrib == null || !isFinite(contrib) || weight === 0) return 0.5
  const w = Math.abs(weight)
  if (weight > 0) return Math.max(0, Math.min(1, contrib / w))
  // negative weight: best = 0, worst = -w; goodness = 1 + contrib / w (in [0, 1])
  return Math.max(0, Math.min(1, 1 + contrib / w))
}

function factorColor(strike: DitmStrikeInfo | undefined, key: string): string {
  if (!strike) return ''
  const w = V4_FACTOR_WEIGHTS[key]
  if (w == null) return ''
  const contrib = strike.factor_breakdown?.[key]
  if (contrib == null) return '#64748b' // unobserved
  const g = factorGoodness(contrib, w)
  return g >= 0.7 ? '#4ade80' : g >= 0.4 ? '#fbbf24' : '#f87171'
}

function factorBadge(strike: DitmStrikeInfo | undefined, key: string) {
  if (!strike) return null
  const w = V4_FACTOR_WEIGHTS[key]
  if (w == null) return null
  const contrib = strike.factor_breakdown?.[key]
  // Hide badge entirely when contribution is unobserved (small-universe POST path
  // doesn't populate factor_breakdown; rendering "n/a" everywhere is pure noise).
  if (contrib == null) return null
  const g = factorGoodness(contrib, w)
  const color = g >= 0.7 ? '#4ade80' : g >= 0.4 ? '#fbbf24' : '#f87171'
  return (
    <span style={{ fontSize: '10px', color, display: 'block', lineHeight: 1.2 }}>
      {Math.round(g * 100)}%
    </span>
  )
}

// Top N drags: lowest-goodness observed factors on this strike.
function topDrags(strike: DitmStrikeInfo | undefined, n = 2): { key: string; goodness: number }[] {
  if (!strike?.factor_breakdown) return []
  const ranked: { key: string; goodness: number }[] = []
  for (const [k, w] of Object.entries(V4_FACTOR_WEIGHTS)) {
    const c = strike.factor_breakdown[k]
    if (c == null) continue
    ranked.push({ key: k, goodness: factorGoodness(c, w) })
  }
  return ranked.sort((a, b) => a.goodness - b.goodness).slice(0, n)
}

// ---------------------------------------------------------------------------
// Score colour (v4 tier bands: A≥90, B≥70, C≥50, D≥30, E<30)
// ---------------------------------------------------------------------------

const TIER_COLORS: Record<string, string> = {
  A: '#4ade80', B: '#86efac', C: '#facc15', D: '#fb923c', E: '#f87171',
}

function tierForScore(score: number | undefined | null): 'A' | 'B' | 'C' | 'D' | 'E' {
  if (score == null) return 'E'
  if (score >= 90) return 'A'
  if (score >= 70) return 'B'
  if (score >= 50) return 'C'
  if (score >= 30) return 'D'
  return 'E'
}

function scoreFmt(
  env: number | undefined,
  strike: number | undefined,
  final: number | undefined,
  tier?: string | null,
  highlight = false,
) {
  if (final == null || isNaN(final)) return <span className="dim">—</span>
  const t = (tier as 'A'|'B'|'C'|'D'|'E') ?? tierForScore(final)
  const color = TIER_COLORS[t] ?? '#94a3b8'
  return (
    <span
      style={{
        color,
        fontWeight: highlight ? 800 : 700,
        fontSize: highlight ? '15px' : undefined,
      }}
      title={`Tier ${t}  ·  Score: ${final.toFixed(0)}  ·  Val+Cap+Macro pillar pctile: ${env?.toFixed(0) ?? '—'}  ·  Tech+Option pillar pctile: ${strike?.toFixed(0) ?? '—'}`}
    >
      {final.toFixed(0)}
      <span style={{ fontSize: '11px', marginLeft: 4, opacity: 0.85 }}>{t}</span>
    </span>
  )
}

// ---------------------------------------------------------------------------
// Grouping
// ---------------------------------------------------------------------------

// Ticker-level columns (header rendering / sorting only; cells rendered via rowSpan)
const COLUMNS = [
  col.accessor('symbol',                 { header: 'Symbol',   cell: () => null, meta: { sticky: 1 } }),
  col.accessor('price',                  { header: 'Price',    cell: () => null, meta: { sticky: 2 } }),
  col.accessor('weekly_rsi',             { header: () => <span className="col-tip col-scored" title="Weekly RSI(14) — medium-term momentum on weekly closes · lower = better (pullback in uptrend, v4 sign −)">W-RSI ⓘ</span>, cell: () => null }),
  col.accessor('ret_200d',               { header: () => <span className="col-tip col-scored" title="200-day median-anchored return · higher = better (v4 sign +)">200d Ret ⓘ</span>, cell: () => null }),
  col.accessor('dist_from_52w_high_pct', { header: () => <span className="col-tip col-scored" title="Distance from 52-week high · deeper pullback ranks higher (v4 sign −)">52W Dist ⓘ</span>, cell: () => null }),
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
        best_tier: null,
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
    g.best_tier = bestStrike?.tier ?? null
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

  // v4 (ADR-0032): scores are cross-sectional percentiles. With a small candidate
  // pool the ranking is meaningless — every strike floats to the top. Count distinct
  // tickers and total strikes to decide whether to warn.
  const tickerCount = groupedData.length
  const totalStrikes = groupedData.reduce(
    (sum, r) => sum + r.expirations.reduce((s, e) => s + e.strikes.length, 0),
    0,
  )
  const smallUniverse = tickerCount < 5 || totalStrikes < 20

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
      {smallUniverse && (
        <div
          className="stale-banner"
          style={{ background: 'rgba(251,191,36,0.10)', borderColor: '#fbbf24' }}
          title="v4 ranks candidates against each other; tiny pools produce inflated scores."
        >
          <span style={{ color: '#fbbf24' }}>
            ⚠ Small universe ({tickerCount} ticker{tickerCount === 1 ? '' : 's'}, {totalStrikes} strike{totalStrikes === 1 ? '' : 's'})
          </span>
          <span style={{ color: '#94a3b8', marginLeft: 10, fontSize: '12px' }}>
            Scores are universe-relative percentiles — with this few candidates the rankings inflate (the best one is always near 100). Use Auto Scan on S&amp;P 100+ for comparable A/B/C/D/E tiers.
          </span>
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
              <th
                className="sortable"
                onClick={() => scoreCol?.toggleSorting(scoreSorted === 'asc')}
              >
                <span className="col-tip" title="v4 score = candidate's percentile within the scored universe.&#10;Tier band: A≥90 · B≥70 · C≥50 · D≥30 · E<30.&#10;&#10;Cross-sectional rank-and-blend across 13 factors in 5 pillars:&#10;  Valuation (cap 35%)  P/S, EV/S, EV/EBITDA  (lower ranks better)&#10;  Option    (cap 25%)  Leverage, Delta, Extrinsic%&#10;  Technical (cap 20%)  W-RSI, 52W Dist, HV30, 200d Ret&#10;  Capital   (cap 15%)  D/E, ND/EBITDA  (higher ranks better)&#10;  Macro     (cap 5%)   Sector RS  (inert until wired)&#10;&#10;Per-group budget split by |IC| vs realised forward ROC.&#10;Min 8 of 13 observed; missing factors median-imputed.&#10;See ADR-0032 for calibration evidence.">
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

            // v4: ticker-level cells color by the best strike's factor_breakdown
            // (the technical/sector factors are stock-level so they're identical across strikes).
            const tickerSrc = isFirstRow ? bestStrike : undefined

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

                  {/* Weekly RSI — v4: scored factor wk_rsi (sign −) */}
                  <td rowSpan={totalRows}>
                    {isNaN(r.weekly_rsi)
                      ? <span className="dim">—</span>
                      : <>
                          <span style={{ color: factorColor(tickerSrc, 'wk_rsi') }}>
                            {r.weekly_rsi.toFixed(1)}
                          </span>
                          {factorBadge(tickerSrc, 'wk_rsi')}
                        </>
                    }
                  </td>

                  {/* 200d Return — v4: scored factor ret_200d (sign +) */}
                  <td rowSpan={totalRows}>
                    {isNaN(r.ret_200d)
                      ? <span className="dim">—</span>
                      : <>
                          <span style={{ color: factorColor(tickerSrc, 'ret_200d') }}>
                            {r.ret_200d >= 0 ? '+' : ''}{r.ret_200d.toFixed(1)}%
                          </span>
                          {factorBadge(tickerSrc, 'ret_200d')}
                        </>
                    }
                  </td>

                  {/* 52W Dist — v4: scored factor dist52w (sign −, deeper pullback = better) */}
                  <td rowSpan={totalRows}>
                    {isNaN(r.dist_from_52w_high_pct)
                      ? <span className="dim">—</span>
                      : <>
                          <span style={{ color: factorColor(tickerSrc, 'dist52w') }}>
                            {r.dist_from_52w_high_pct.toFixed(1)}%
                          </span>
                          {factorBadge(tickerSrc, 'dist52w')}
                        </>
                    }
                  </td>

                  {/* Earnings — v4: display-only badge (no longer a scored penalty) */}
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
                            return (
                              <span
                                style={{ fontSize: '10px', color: tone, display: 'block', lineHeight: 1.2 }}
                                title="v4 surfaces earnings for awareness only — no scoring penalty"
                              >
                                {dte}d
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
                  <span style={{ color: factorColor(bestStrike, 'delta') }}>
                    +{fmtDelta(bestStrike.delta)}
                  </span>
                  {bestStrike.iv_fallback && (
                    <span className="iv-fallback-tag" title="Delta estimated from historical volatility — market closed/stale quotes">~HV</span>
                  )}
                  {factorBadge(bestStrike, 'delta')}
                </td>

                {/* Leverage — v4: scored factor leverage (sign +) */}
                <td>
                  {(() => {
                    const lev = bestStrike.mid > 0 ? bestStrike.delta * r.price / bestStrike.mid : 0
                    return <>
                      <span style={{ color: factorColor(bestStrike, 'leverage') }}>
                        {lev.toFixed(2)}×
                      </span>
                      {factorBadge(bestStrike, 'leverage')}
                    </>
                  })()}
                </td>

                <td>
                  <span style={{ color: factorColor(bestStrike, 'extrinsic_pct') }}>
                    {fmtPct(bestStrike.extrinsic_pct)}
                  </span>
                  {factorBadge(bestStrike, 'extrinsic_pct')}
                </td>

                <td>
                  {scoreFmt(bestStrike.env_score, bestStrike.strike_score, bestStrike.ditm_score, bestStrike.tier, true)}
                </td>
                <td>
                  {topDrags(bestStrike).map(d => (
                    <span key={d.key} style={{ display: 'block', fontSize: '12px', color: d.goodness <= 0.2 ? '#f87171' : '#fb923c' }}>
                      {V4_FACTOR_LABELS[d.key] ?? d.key} {Math.round(d.goodness * 100)}%
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
                      <span style={{ color: factorColor(s, 'delta') }}>
                        +{fmtDelta(s.delta)}
                      </span>
                      {s.iv_fallback && (
                        <span className="iv-fallback-tag" title="Delta estimated from historical volatility">~HV</span>
                      )}
                      {factorBadge(s, 'delta')}
                    </td>
                    {/* Leverage */}
                    <td>
                      {(() => {
                        const lev = s.mid > 0 ? s.delta * r.price / s.mid : 0
                        return <>
                          <span style={{ color: factorColor(s, 'leverage') }}>
                            {lev.toFixed(2)}×
                          </span>
                          {factorBadge(s, 'leverage')}
                        </>
                      })()}
                    </td>
                    <td>
                      <span style={{ color: factorColor(s, 'extrinsic_pct') }}>
                        {fmtPct(s.extrinsic_pct)}
                      </span>
                      {factorBadge(s, 'extrinsic_pct')}
                    </td>
                    <td>
                      {scoreFmt(s.env_score, s.strike_score, s.ditm_score, s.tier)}
                    </td>
                    <td>
                      {topDrags(s).map(d => (
                        <span key={d.key} style={{ display: 'block', fontSize: '12px', color: d.goodness <= 0.2 ? '#f87171' : '#fb923c' }}>
                          {V4_FACTOR_LABELS[d.key] ?? d.key} {Math.round(d.goodness * 100)}%
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
        v4 (ADR-0032): Cross-sectional rank-and-blend across 13 factors in 5 groups. Score is universe percentile;
        tier band: A≥90 · B≥70 · C≥50 · D≥30 · E&lt;30. ⚠ in Symbol = overnight gap ≥ 3% in last 3 sessions.
      </div>
    </div>
  )
}

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
import type { ScreenerResult, GroupedScreenerResult } from '../types/screener'

const col = createColumnHelper<GroupedScreenerResult>()

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

// Ticker-level columns — for header rendering + sorting only.
// Cells are rendered manually in the tbody via rowSpan.
const COLUMNS = [
  col.accessor('symbol', { header: 'Symbol', cell: () => null }),
  col.accessor('price', { header: 'Price', cell: () => null }),
  col.accessor('bb_lower', {
    header: () => (
      <span className="col-tip" title="Bollinger Bands (20, 2σ)  ·  Upper / Middle / Lower">
        BB Bands ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('vol_support_1', {
    header: 'Vol Support',
    cell: () => null,
    enableSorting: false,
  }),
  col.accessor('sma_ratio', {
    header: () => (
      <span className="col-tip" title="SMA50 / SMA200 ratio (display)  ·  Score uses: Price>SMA50>SMA200 alignment (10 pts) + SMA50 10-day slope (10 pts)">
        SMA50/200 ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('rsi', {
    header: () => (
      <span className="col-tip" title="RSI(14) Wilder-smoothed  ·  >70 overbought  ·  <30 oversold  ·  40–70 ideal for CSP">
        RSI(14) ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('iv_rank', {
    header: () => (
      <span className="col-tip" title="IV Rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100  ·  Score uses IV Rank (15 pts) + IV÷HV Ratio (10 pts; ≥1.5× = full)">
        IV Rank ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('earnings_date', { header: 'Earnings', cell: () => null }),
  // Hidden sort key — excluded from visible headers via columnVisibility
  col.accessor('best_score', { header: () => null, cell: () => null }),
]

function groupResults(results: ScreenerResult[]): GroupedScreenerResult[] {
  const map = new Map<string, GroupedScreenerResult>()
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
        vol_support_1: r.vol_support_1,
        vol_support_2: r.vol_support_2,
        vol_support_3: r.vol_support_3,
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
    })
  }
  for (const g of map.values()) {
    g.expirations.sort((a, b) => a.dte - b.dte)
    g.best_score = Math.max(...g.expirations.map(e => e.best_score))
  }
  return [...map.values()].sort((a, b) => b.best_score - a.best_score)
}

interface Props {
  data: ScreenerResult[]
}

export function ScreenerTable({ data }: Props) {
  const groupedData = useMemo(() => groupResults(data), [data])
  const [sorting, setSorting] = useState<SortingState>([{ id: 'best_score', desc: true }])
  const [strikeExpanded, setStrikeExpanded] = useState<Set<string>>(new Set())
  const [staleDismissed, setStaleDismissed] = useState(false)

  const anyStale = groupedData.some(r => r.using_hv_fallback)

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
  const scoreFmt = (v: number, highlight = false) => {
    const cls = v >= 70 ? 'score-good' : v >= 45 ? 'score-caution' : 'score-bad'
    return <span className={cls} style={highlight ? { fontWeight: 800, fontSize: '15px' } : {}}>{v.toFixed(0)}</span>
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
              {hg.headers.map(header => (
                <th
                  key={header.id}
                  onClick={header.column.getToggleSortingHandler()}
                  className={header.column.getCanSort() ? 'sortable' : ''}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  {header.column.getIsSorted() === 'asc' && ' ↑'}
                  {header.column.getIsSorted() === 'desc' && ' ↓'}
                </th>
              ))}
              <th>DTE</th>
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
              <th>Premium</th>
              <th>Delta</th>
              <th>
                <span className="col-tip" title="(Ask − Bid) / Mid × 100  ·  Lower = tighter market  ·  >10% = illiquid">
                  Spread% ⓘ
                </span>
              </th>
              <th>Ann. Return</th>
              <th
                className="sortable"
                onClick={() => scoreCol?.toggleSorting(scoreSorted === 'asc')}
              >
                <span className="col-tip" title="CSP Score 0–100&#10;&#10;VOLATILITY (25)&#10;  IV Rank         15 pts  ≥50 = full&#10;  IV / HV Ratio   10 pts  ≥1.5× = full&#10;&#10;RETURN (15)&#10;  Ann. Return     10 pts  ≥25% = full&#10;  Prem Efficiency  5 pts  premium ÷ distance to strike&#10;&#10;TREND (20)&#10;  SMA Alignment   10 pts  Price>SMA50>SMA200&#10;  52W High Dist.  10 pts  ≤5% below high = full&#10;&#10;RISK POSITIONING (20)&#10;  Delta           15 pts  peak at −0.225; aggressive 6; low-yield 5&#10;  Expected Move    5 pts  strike outside 1σ move&#10;&#10;MOMENTUM (8)&#10;  RSI(14)          8 pts  40–65 = full&#10;&#10;EXECUTION (12)&#10;  Spread %         8 pts  ≤3% = full&#10;  Open Interest    4 pts  ≥1000 OI/vol = full&#10;&#10;Earnings in DTE −15 pts penalty">
                  Score ⓘ
                </span>
                {scoreSorted === 'asc' && ' ↑'}
                {scoreSorted === 'desc' && ' ↓'}
              </th>
            </tr>
          ))}
        </thead>

        {table.getRowModel().rows.map(row => {
          const r = row.original

          // Pre-compute total <tr> count for rowSpan (main rows + expanded alt rows)
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

            // ── Main row (best strike) ────────────────────────────────────
            rows.push(
              <tr key={`${expIdx}-best`} className={isFirstRow ? 'first-exp-row' : 'sub-exp-row'}>

                {/* Ticker-level cells — only on absolute first row, spanning all rows */}
                {isFirstRow && <>
                  <td rowSpan={totalRows} className="ticker-cell">
                    <strong>{r.symbol}</strong>
                  </td>
                  <td rowSpan={totalRows}>{fmt2(r.price)}</td>
                  <td rowSpan={totalRows}>
                    <span className="bb-bands">
                      <span className="bb-upper">{fmt2(r.bb_upper)}</span>
                      <span className="bb-middle">{fmt2(r.bb_middle)}</span>
                      <span className="bb-lower">{fmt2(r.bb_lower)}</span>
                    </span>
                  </td>
                  <td rowSpan={totalRows}>
                    {(() => {
                      const levels = [r.vol_support_1, r.vol_support_2, r.vol_support_3]
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
                    {r.sma_ratio == null || isNaN(r.sma_ratio)
                      ? <span className="dim">—</span>
                      : <span className={r.sma_ratio >= 1 ? 'positive' : 'negative'}>{r.sma_ratio.toFixed(4)}</span>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.rsi == null || isNaN(r.rsi)
                      ? <span className="dim">—</span>
                      : <span className={r.rsi >= 70 ? 'rsi-high' : r.rsi <= 30 ? 'rsi-low' : 'rsi-ok'}>{r.rsi.toFixed(1)}</span>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.iv_rank == null
                      ? <span className="dim">N/A</span>
                      : <>
                          <span className={r.iv_rank >= 50 ? 'badge badge-green' : r.iv_rank >= 30 ? 'badge badge-yellow' : 'badge badge-red'}>
                            {r.iv_rank.toFixed(0)}
                          </span><br />
                          <span className="expiry-date">P:{r.iv_percentile != null ? r.iv_percentile.toFixed(0) : '—'}</span>
                        </>
                    }
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
                  {altStrikes.length > 0 && (
                    <button className="strike-toggle" onClick={() => toggleStrikes(key)}>
                      {showAlts ? '▲ hide' : `▼ ${altStrikes.length} more`}
                    </button>
                  )}
                </td>
                <td className="prem-cell">${bestStrike.premium.toFixed(2)}</td>
                <td>
                      <span className={bestStrike.delta >= -0.35 && bestStrike.delta <= -0.10 ? 'delta-ok' : 'delta-warn'}>
                    {fmtDelta(bestStrike.delta)}
                  </span>
                  {bestStrike.iv_fallback && <span className="iv-fallback-tag" title="Delta estimated from historical volatility (HV) — market closed/stale quotes">~HV</span>}
                </td>
                <td>{fmtSpread(bestStrike.bid_ask_spread_pct)}</td>
                <td>{fmtAnn(bestStrike.annualized_return)}</td>
                <td>{scoreFmt(bestStrike.csp_score, true)}</td>
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
                    </td>
                    <td className="prem-cell">${s.premium.toFixed(2)}</td>
                    <td>
                      <span className={s.delta >= -0.35 && s.delta <= -0.10 ? 'delta-ok' : 'delta-warn'}>
                        {fmtDelta(s.delta)}
                      </span>
                      {s.iv_fallback && <span className="iv-fallback-tag" title="Delta estimated from historical volatility (HV) — market closed/stale quotes">~HV</span>}
                    </td>
                    <td>{fmtSpread(s.bid_ask_spread_pct)}</td>
                    <td>{fmtAnn(s.annualized_return)}</td>
                    <td>{scoreFmt(s.csp_score)}</td>
                  </tr>
                )
                absRowIdx++
              }
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
        IV Rank/Percentile = HV-based proxy (252-day window). P: = IV Percentile. Best strike highlighted by highest CSP score.
      </p>
    </div>
  )
}

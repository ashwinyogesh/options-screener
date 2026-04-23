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

const COLUMNS = [
  col.accessor('symbol',    { header: 'Symbol',   cell: () => null }),
  col.accessor('price',     { header: 'Price',    cell: () => null }),
  col.accessor('vol_resistance_1', {
    header: () => (
      <span className="col-tip" title="Volume Profile resistance levels above current price (top-3 high-volume bins)">
        Vol Resistance ⓘ
      </span>
    ),
    cell: () => null,
    enableSorting: false,
  }),
  col.accessor('sma_ratio', {
    header: () => (
      <span className="col-tip" title="SMA50 / SMA200 ratio  ·  >1 = bullish alignment  ·  Required for DITM">
        SMA50/200 ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('rsi', {
    header: () => (
      <span className="col-tip" title="RSI(14) Wilder-smoothed  ·  45–68 ideal for uptrend momentum  ·  >78 = overbought">
        RSI(14) ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('iv_rank', {
    header: () => (
      <span className="col-tip" title="IV Rank: how far today's IV sits between the 252d min and max (magnitude of the move).&#10;IV Percentile (P:): % of past days where IV was cheaper than today (frequency).&#10;&#10;For DITM: LOW rank = cheap options = better to buy">
        IV Rank ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('earnings_date', { header: 'Earnings', cell: () => null }),
  col.accessor('best_score',    { header: () => null, cell: () => null }),
]

function groupResults(results: DitmResult[]): GroupedDitmResult[] {
  const map = new Map<string, GroupedDitmResult>()
  for (const r of results) {
    if (!map.has(r.symbol)) {
      map.set(r.symbol, {
        symbol: r.symbol,
        price: r.price,
        sma_ratio: r.sma_ratio,
        rsi: r.rsi,
        iv_rank: r.iv_rank,
        iv_percentile: r.iv_percentile,
        earnings_date: r.earnings_date,
        earnings_within_dte: false,
        vol_resistance_1: r.vol_resistance_1,
        vol_resistance_2: r.vol_resistance_2,
        vol_resistance_3: r.vol_resistance_3,
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
      best_score: r.best_ditm_score,
      using_hv_fallback: r.using_hv_fallback,
    })
  }
  for (const g of map.values()) {
    g.expirations.sort((a, b) => a.dte - b.dte)
    g.best_score = Math.max(...g.expirations.map(e => e.best_score))
  }
  return [...map.values()].sort((a, b) => b.best_score - a.best_score)
}

const fmtSpread = (v: number | null) => {
  if (v == null) return <span className="dim">—</span>
  const cls = v > 10 ? 'spread-wide' : v > 5 ? 'spread-ok' : 'spread-tight'
  return <span className={cls}>{v.toFixed(1)}%</span>
}

interface Props {
  data: DitmResult[]
}

export function DitmTable({ data }: Props) {
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

  const scoreFmt = (env: number | undefined, strike: number | undefined, final: number | undefined, highlight = false) => {
    if (final == null || isNaN(final)) return <span className="dim">—</span>
    const cls = final >= 70 ? 'score-good' : final >= 45 ? 'score-caution' : 'score-bad'
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

  return (
    <div className="table-wrapper">
      {anyStale && !staleDismissed && (
        <div className="stale-banner">
          <span>⚠ Market closed — options quotes are stale. Delta estimated from 30-day historical volatility. Treat values as approximate.</span>
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
              <th>Strike</th>
              <th>Premium</th>
              <th>
                <span className="col-tip" title="Delta of the call option  ·  0.80–0.85 = DITM sweet spot  ·  High delta = stock substitute">
                  Delta ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Intrinsic = max(0, Price − Strike)  ·  Pure stock value embedded in the option">
                  Intrinsic ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Extrinsic % = (Premium − Intrinsic) / Stock Price × 100  ·  This is the time premium you overpay — it decays to zero by expiration">
                  Extrinsic% ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Leverage = Stock Price / Premium  ·  e.g. 3.5× means you control $100 of stock for $28.50  ·  Higher leverage = more price exposure per $ spent">
                  Leverage ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="(Ask − Bid) / Mid × 100  ·  Deep ITM calls are illiquid — wide spreads are common">
                  Spread% ⓘ
                </span>
              </th>
              <th
                className="sortable"
                onClick={() => scoreCol?.toggleSorting(scoreSorted === 'asc')}
              >
                <span className="col-tip" title="Final Score = 0.35×Env + 0.65×Strike&#10;&#10;ENV SCORE (100 pts) — inverted for buyers&#10;  IV Rank (inv.)     25 pts  <20 = full (cheap options)&#10;  IV/HV Ratio (inv.) 20 pts  <0.8 = full&#10;  SMA Alignment      20 pts  trend required&#10;  52W High Dist.     15 pts  ≤5% below = full&#10;  RSI(14)            10 pts  45–68 = full&#10;  Chain Median OI    10 pts  log scale&#10;  Earnings in DTE   −15 pts  penalty&#10;&#10;STRIKE SCORE (100 pts)&#10;  Delta              30 pts  peak 0.80–0.85&#10;  Extrinsic %        30 pts  ≤1% = full&#10;  Moneyness %        15 pts  ≥15% ITM = full&#10;  Bid-Ask Spread     15 pts  ≤1% = full&#10;  OI / Volume        10 pts  ≥500 = full">
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

            rows.push(
              <tr key={`${expIdx}-best`} className={isFirstRow ? 'first-exp-row' : 'sub-exp-row'}>
                {isFirstRow && <>
                  <td rowSpan={totalRows} className="ticker-cell">
                    <strong>{r.symbol}</strong>
                  </td>
                  <td rowSpan={totalRows}>{fmt2(r.price)}</td>
                  <td rowSpan={totalRows}>
                    {(() => {
                      const levels = [r.vol_resistance_1, r.vol_resistance_2, r.vol_resistance_3]
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
                      : <span className={r.rsi >= 78 ? 'rsi-high' : r.rsi <= 30 ? 'rsi-low' : 'rsi-ok'}>{r.rsi.toFixed(1)}</span>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.iv_rank == null
                      ? <span className="dim">N/A</span>
                      : <>
                          {/* For DITM: LOW IV rank is good — invert badge colors */}
                          <span className={r.iv_rank < 30 ? 'badge badge-green' : r.iv_rank < 50 ? 'badge badge-yellow' : 'badge badge-red'}>
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

                <td className="dte-cell" rowSpan={dteCellRows}>
                  <span className="dte-num">{exp.dte}</span><br />
                  <span className="expiry-date">{exp.expiration}</span>
                  {exp.earnings_within_dte && <span className="earnings-warn"> ⚠</span>}
                </td>

                {/* Best strike row */}
                <td className="strike-cell best-strike">
                  <span className="strike-price">{fmt2(bestStrike.strike)}</span>
                  <span className="strike-fall" style={{ color: '#94a3b8' }}>
                    {' '}{bestStrike.moneyness_pct.toFixed(1)}% ITM
                  </span>
                  {altStrikes.length > 0 && (
                    <button className="strike-toggle" onClick={() => toggleStrikes(key)}>
                      {showAlts ? '▲ hide' : `▼ ${altStrikes.length} more`}
                    </button>
                  )}
                </td>
                <td className="prem-cell">${bestStrike.premium.toFixed(2)}</td>
                <td>
                  <span className={bestStrike.delta >= 0.65 && bestStrike.delta <= 0.95 ? 'delta-ok' : 'delta-warn'}>
                    {bestStrike.delta.toFixed(3)}
                  </span>
                  {bestStrike.iv_fallback && <span className="hv-tag" title="Delta estimated from 30d HV"> ~HV</span>}
                </td>
                <td>${bestStrike.intrinsic.toFixed(2)}</td>
                <td>
                  <span className={bestStrike.extrinsic_pct <= 2 ? 'score-good' : bestStrike.extrinsic_pct <= 5 ? 'score-caution' : 'score-bad'}>
                    {bestStrike.extrinsic_pct.toFixed(1)}%
                  </span>
                </td>
                <td>
                  <span className="leverage-badge">{bestStrike.leverage.toFixed(1)}×</span>
                </td>
                <td>{fmtSpread(bestStrike.bid_ask_spread_pct)}</td>
                <td>{scoreFmt(bestStrike.env_score, bestStrike.strike_score, bestStrike.ditm_score, true)}</td>
              </tr>
            )

            if (showAlts) {
              for (const s of altStrikes) {
                rows.push(
                  <tr key={`${expIdx}-${s.strike}`} className="alt-strike-row">
                    <td className="strike-cell">
                      <span className="strike-price">{fmt2(s.strike)}</span>
                      <span className="strike-fall" style={{ color: '#94a3b8' }}>
                        {' '}{s.moneyness_pct.toFixed(1)}% ITM
                      </span>
                    </td>
                    <td className="prem-cell">${s.premium.toFixed(2)}</td>
                    <td>
                      <span className={s.delta >= 0.65 && s.delta <= 0.95 ? 'delta-ok' : 'delta-warn'}>
                        {s.delta.toFixed(3)}
                      </span>
                      {s.iv_fallback && <span className="hv-tag"> ~HV</span>}
                    </td>
                    <td>${s.intrinsic.toFixed(2)}</td>
                    <td>
                      <span className={s.extrinsic_pct <= 2 ? 'score-good' : s.extrinsic_pct <= 5 ? 'score-caution' : 'score-bad'}>
                        {s.extrinsic_pct.toFixed(1)}%
                      </span>
                    </td>
                    <td>
                      <span className="leverage-badge">{s.leverage.toFixed(1)}×</span>
                    </td>
                    <td>{fmtSpread(s.bid_ask_spread_pct)}</td>
                    <td>{scoreFmt(s.env_score, s.strike_score, s.ditm_score)}</td>
                  </tr>
                )
                absRowIdx++
              }
            }

            absRowIdx++
          }

          return <tbody key={r.symbol}>{rows}</tbody>
        })}
      </table>
      <div className="table-footer-note">
        IV Rank/Percentile = HV-based proxy (252-day window). For DITM: <strong>low IV rank = cheaper options</strong> (inverted badge — green = low). P: = IV Percentile. Extrinsic% = time premium paid as % of stock price. Best strike per expiry highlighted.
      </div>
    </div>
  )
}

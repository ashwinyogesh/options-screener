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
      <span className="col-tip" title="Volume Profile resistance levels above the current price (252-day lookback) · High-volume price bins where sellers historically appeared · Context for your long call's upside ceiling">
        Vol Resistance ⓘ
      </span>
    ),
    cell: () => null,
    enableSorting: false,
  }),
  col.accessor('sma_ratio', {
    header: () => (
      <span className="col-tip col-scored" title="SMA50 ÷ SMA200 · Ratio >1 means the 50-day average is above the 200-day average (bullish structure)">
        SMA50/200 ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('rsi', {
    header: () => (
      <span className="col-tip" title="Relative Strength Index (14-period) · Momentum oscillator on a 0–100 scale · >70 overbought · <30 oversold">
        RSI(14) ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('iv_rank', {
    header: () => (
      <span className="col-tip" title="IV Rank: where today's implied volatility sits within its 252-day min–max range (0 = historically cheap, 100 = historically expensive)">
        IV Rank ⓘ
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
  col.accessor('iv_hv_ratio', {
    header: () => (
      <span className="col-tip col-scored" title="Implied Volatility ÷ 30-day Historical Volatility · >1.0 = options priced above recent realized moves · <1.0 = options relatively cheap (favorable for buyers)">
        IV/HV ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('trend_persistence', {
    header: () => (
      <span className="col-tip col-scored" title="% of the last 60 sessions where price closed above the SMA50 · Measures trend consistency">
        Trend% ⓘ
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
        dist_from_52w_high_pct: r.dist_from_52w_high_pct,
        iv_hv_ratio: null,
        trend_persistence: r.trend_persistence,
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
    const bs = g.expirations.flatMap(e => e.strikes).find(s => s.is_best) ?? g.expirations[0]?.strikes[0]
    g.iv_hv_ratio = bs?.iv_hv_ratio ?? null
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
              <th>
                <span className="col-tip" title="Days to Expiration · Time remaining until the option contract expires">
                  DTE ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Strike price of the call option · Chosen deep in-the-money, well below the current stock price">
                  Strike ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Option mid-price: (Bid + Ask) ÷ 2 · Per-share price; multiply by 100 for full contract cost">
                  Premium ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Delta: rate of change of option price per $1 move in the stock · 0.80+ = deep in-the-money, behaves closely like the stock">
                  Delta ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Intrinsic value: max(0, Stock Price − Strike) · The equity value embedded in the option">
                  Intrinsic ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Extrinsic value as % of stock price: (Premium − Intrinsic) ÷ Stock Price × 100 · Time premium that decays to zero by expiration">
                  Extrinsic% ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Stock Price ÷ Premium · Capital efficiency vs buying shares outright · e.g. 3.5× = you control $100 of stock for $28.57">
                  Leverage ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Bid-ask spread as % of mid-price: (Ask − Bid) ÷ Mid × 100 · Reflects execution cost and liquidity">
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
                          </span>
                        </>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {isNaN(r.dist_from_52w_high_pct)
                      ? <span className="dim">—</span>
                      : <span className={r.dist_from_52w_high_pct >= -5 ? 'score-good' : r.dist_from_52w_high_pct >= -15 ? 'score-caution' : 'score-bad'}>
                          {r.dist_from_52w_high_pct.toFixed(1)}%
                        </span>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.iv_hv_ratio == null
                      ? <span className="dim">—</span>
                      : <span className={r.iv_hv_ratio < 0.8 ? 'score-good' : r.iv_hv_ratio < 1.1 ? 'score-caution' : 'score-bad'}>
                          {r.iv_hv_ratio.toFixed(2)}×
                        </span>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.trend_persistence == null
                      ? <span className="dim">—</span>
                      : <span className={r.trend_persistence >= 75 ? 'score-good' : r.trend_persistence >= 50 ? 'score-caution' : 'score-bad'}>
                          {r.trend_persistence.toFixed(0)}%
                        </span>
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
        IV Rank = HV-based proxy (252-day window). For DITM: <strong>low IV rank = cheaper options</strong> (inverted badge — green = low). Extrinsic% = time premium paid as % of stock price. Best strike per expiry highlighted.
      </div>
    </div>
  )
}

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
import type { CcResult, GroupedCcResult } from '../types/cc'

const col = createColumnHelper<GroupedCcResult>()

function fmt2(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}
function fmtAnn(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(1) + '%'
}

function parseEnvDetail(detail: string): Record<string, number> {
  const out: Record<string, number> = {}
  for (const part of (detail ?? '').split(' ')) {
    const idx = part.indexOf(':')
    if (idx > 0) out[part.slice(0, idx)] = Number(part.slice(idx + 1))
  }
  return out
}
const ENV_MAX: Record<string, number> = { IV: 25, IH: 20, SMA: 15, '52W': 15, RSI: 10 }
function envSub(pts: Record<string, number>, key: string) {
  const v = pts[key], max = ENV_MAX[key]
  if (v == null || max == null) return null
  const ratio = v / max
  const color = ratio >= 0.70 ? '#4ade80' : ratio >= 0.45 ? '#fbbf24' : '#f87171'
  return <span style={{ fontSize: '10px', color, display: 'block', lineHeight: 1.2 }}>{Math.round(v)}/{max}</span>
}

const COLUMNS = [
  col.accessor('symbol',        { header: 'Symbol',     cell: () => null }),
  col.accessor('price',         { header: 'Price',      cell: () => null }),
  col.accessor('bb_lower', {
    header: () => (
      <span className="col-tip" title="Bollinger Bands (20-period, 2σ) · Upper / Middle / Lower band around the closing price">
        BB Bands ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('sma_ratio',     {
    header: () => (
      <span className="col-tip col-scored" title="SMA50 ÷ SMA200 · Ratio >1 means the 50-day average is above the 200-day average (bullish structure)">
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
  col.accessor('iv_rank',       {
    header: () => (
      <span className="col-tip col-scored" title="IV Rank: where today's implied volatility sits within its 252-day min–max range (0 = historically cheap, 100 = historically expensive)">
        IV Rank ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('iv_hv_ratio', {
    header: () => (
      <span className="col-tip col-scored" title="Implied Volatility ÷ 30-day Historical Volatility · >1.0 = options priced above recent realized moves · <1.0 = options relatively cheap">
        IV/HV ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('rsi',           {
    header: () => (
      <span className="col-tip col-scored" title="Relative Strength Index (14-period) · Momentum oscillator on a 0–100 scale · >70 overbought · <30 oversold">
        RSI(14) ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('vol_resistance_126_1', {
    header: () => (
      <span className="col-tip col-scored" title="Volume Profile resistance levels above the current price (126-day / 6M lookback) · High-volume price bins where sellers historically appeared">
        Vol Resistance 6M ⓘ
      </span>
    ),
    cell: () => null,
    enableSorting: false,
  }),
  col.accessor('earnings_date', { header: 'Earnings',   cell: () => null }),
  col.accessor('best_score',    { header: () => null,   cell: () => null }),
]

function groupResults(results: CcResult[]): GroupedCcResult[] {
  const map = new Map<string, GroupedCcResult>()
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
        vol_resistance_126_1: r.vol_resistance_126_1,
        vol_resistance_126_2: r.vol_resistance_126_2,
        vol_resistance_126_3: r.vol_resistance_126_3,
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
      best_score: r.best_cc_score,
      using_hv_fallback: r.using_hv_fallback,
      expected_move: r.expected_move,
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
  data: CcResult[]
}

export function CcTable({ data }: Props) {
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

  const scoreFmt = (env: number | undefined, strike: number | undefined, final: number | undefined, envDetail?: string, strikeDetail?: string, highlight = false) => {
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
              <th>
                <span className="col-tip" title="Days to Expiration  ·  Number of calendar days remaining until the option expires  ·  Score uses expirations within your min–max DTE range">
                  DTE ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Expected Move = price × HV(30d) × √(DTE/365)  ·  1σ range  ·  Ceiling = price + EM">
                  Exp. Move ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Call strike above current price  ·  Best score highlighted  ·  ▼ N more reveals all strikes">
                  Strike ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Option mid-price: (Bid + Ask) / 2  ·  Falls back to last-traded price if bid/ask = 0 (market closed)  ·  Per contract = × 100 shares">
                  Premium ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Black-Scholes call delta  ·  Positive for calls  ·  Approximates probability of expiring in-the-money (shares called away)  ·  +0.20 to +0.25 = sweet spot (20–25% assignment chance)">
                  Delta ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="(Ask − Bid) / Mid × 100  ·  Lower = tighter market">
                  Spread% ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="(Premium / Stock Price) × (365 / DTE) × 100  ·  Annualized yield on the shares you already own  ·  Additional income on top of any capital appreciation">
                  Ann. Return ⓘ
                </span>
              </th>
              <th
                className="sortable"
                onClick={() => scoreCol?.toggleSorting(scoreSorted === 'asc')}
              >
                <span className="col-tip" title="Final Score = 0.4×Env + 0.6×Strike&#10;&#10;ENV SCORE (100 pts)&#10;  IV Rank           25 pts&#10;  IV / HV Ratio     20 pts&#10;  SMA Alignment     15 pts&#10;  52W High Dist.    15 pts&#10;  RSI(14)           10 pts&#10;  Chain Median OI   15 pts&#10;  Earnings in DTE  −15 pts&#10;&#10;STRIKE SCORE (100 pts)&#10;  Delta             18 pts  peak +0.20→+0.25&#10;  Dist vs Resist.   13 pts  strike ≥ resistance&#10;  Exp Move Buffer   15 pts  ≥0.2σ above ceiling&#10;  % OTM from Spot   12 pts  ≥15%=full&#10;  Bid-Ask Spread    22 pts  ≤1%=full&#10;  OI / Volume       20 pts  ≥1000=full">
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
            const envPts = isFirstRow ? parseEnvDetail(r.env_detail) : {}

            rows.push(
              <tr key={`${expIdx}-best`} className={isFirstRow ? 'first-exp-row' : 'sub-exp-row'}>

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
                    {r.sma_ratio == null || isNaN(r.sma_ratio)
                      ? <span className="dim">—</span>
                      : <><span className={r.sma_ratio >= 1 ? 'positive' : 'negative'}>{r.sma_ratio.toFixed(4)}</span><br />{envSub(envPts, 'SMA')}</>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {isNaN(r.dist_from_52w_high_pct)
                      ? <span className="dim">—</span>
                      : <><span className={r.dist_from_52w_high_pct >= -5 ? 'score-good' : r.dist_from_52w_high_pct >= -15 ? 'score-caution' : 'score-bad'}>
                          {r.dist_from_52w_high_pct.toFixed(1)}%
                        </span><br />{envSub(envPts, '52W')}</>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.iv_rank == null
                      ? <span className="dim">N/A</span>
                      : <><span className={r.iv_rank >= 50 ? 'badge badge-green' : r.iv_rank >= 30 ? 'badge badge-yellow' : 'badge badge-red'}>
                            {r.iv_rank.toFixed(0)}
                          </span><br />{envSub(envPts, 'IV')}</>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.iv_hv_ratio == null
                      ? <span className="dim">—</span>
                      : <><span className={r.iv_hv_ratio >= 1.4 ? 'score-good' : r.iv_hv_ratio >= 1.0 ? 'score-caution' : 'score-bad'}>
                          {r.iv_hv_ratio.toFixed(2)}×
                        </span><br />{envSub(envPts, 'IH')}</>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.rsi == null || isNaN(r.rsi)
                      ? <span className="dim">—</span>
                      : <><span className={r.rsi >= 70 ? 'rsi-high' : r.rsi <= 30 ? 'rsi-low' : 'rsi-ok'}>{r.rsi.toFixed(1)}</span><br />{envSub(envPts, 'RSI')}</>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {(() => {
                      const levels = [r.vol_resistance_126_1, r.vol_resistance_126_2, r.vol_resistance_126_3]
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

                <td className="dte-cell" rowSpan={dteCellRows}>
                  <span className="dte-num">{exp.dte}</span><br />
                  <span className="expiry-date">{exp.expiration}</span>
                  {exp.earnings_within_dte && <span className="earnings-warn"> ⚠</span>}
                </td>
                <td className="em-cell" rowSpan={dteCellRows}>
                  {exp.expected_move > 0
                    ? <>
                        <span className="em-range">±${exp.expected_move.toFixed(2)}</span><br />
                        <span className="em-floor" title="Upper bound of 1σ expected range">↑ {(r.price + exp.expected_move).toFixed(2)}</span>
                      </>
                    : <span className="dim">—</span>
                  }
                </td>

                <td className="strike-cell best-strike">
                  <span className="strike-price">{fmt2(bestStrike.strike)}</span>
                  <span className="strike-fall" style={{ color: '#4ade80' }}> +{((bestStrike.strike - r.price) / r.price * 100).toFixed(1)}%</span>
                  {altStrikes.length > 0 && (
                    <button className="strike-toggle" onClick={() => toggleStrikes(key)}>
                      {showAlts ? '▲ hide' : `▼ ${altStrikes.length} more`}
                    </button>
                  )}
                </td>
                <td className="prem-cell">${bestStrike.premium.toFixed(2)}</td>
                <td>
                  <span className={bestStrike.delta >= 0.10 && bestStrike.delta <= 0.35 ? 'delta-ok' : 'delta-warn'}>
                    +{bestStrike.delta.toFixed(3)}
                  </span>
                  {bestStrike.iv_fallback && <span className="hv-tag" title="Delta estimated from 30d HV (IV unavailable)"> ~HV</span>}
                </td>
                <td>{fmtSpread(bestStrike.bid_ask_spread_pct)}</td>
                <td>{fmtAnn(bestStrike.annualized_return)}</td>
                <td>{scoreFmt(bestStrike.env_score, bestStrike.strike_score, bestStrike.cc_score, bestStrike.env_detail, bestStrike.strike_detail, true)}</td>
              </tr>
            )

            if (showAlts) {
              for (const s of altStrikes) {
                rows.push(
                  <tr key={`${expIdx}-${s.strike}`} className="alt-strike-row">
                    <td className="strike-cell">
                      <span className="strike-price">{fmt2(s.strike)}</span>
                      <span className="strike-fall" style={{ color: '#4ade80' }}> +{((s.strike - r.price) / r.price * 100).toFixed(1)}%</span>
                    </td>
                    <td className="prem-cell">${s.premium.toFixed(2)}</td>
                    <td>
                      <span className={s.delta >= 0.10 && s.delta <= 0.35 ? 'delta-ok' : 'delta-warn'}>
                        +{s.delta.toFixed(3)}
                      </span>
                      {s.iv_fallback && <span className="hv-tag"> ~HV</span>}
                    </td>
                    <td>{fmtSpread(s.bid_ask_spread_pct)}</td>
                    <td>{fmtAnn(s.annualized_return)}</td>
                    <td>{scoreFmt(s.env_score, s.strike_score, s.cc_score, s.env_detail, s.strike_detail)}</td>
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
    </div>
  )
}

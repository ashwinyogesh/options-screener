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
import type { EmRankResult, GroupedEmRankResult } from '../types/emRank'

const col = createColumnHelper<GroupedEmRankResult>()

function fmt2(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}
function fmtDelta(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(3)
}

// Ticker-level columns — for header rendering + sorting only.
// Cells are rendered manually in tbody via rowSpan.
const COLUMNS = [
  col.accessor('symbol', { header: 'Symbol', cell: () => null, meta: { sticky: 1 } }),
  col.accessor('price', { header: 'Price', cell: () => null, meta: { sticky: 2 } }),
  col.accessor('bb_lower', {
    header: () => (
      <span className="col-tip" title="Bollinger Bands (20-period, 2σ) · Upper / Middle / Lower band">
        BB Bands ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('sma_ratio', {
    header: () => (
      <span className="col-tip" title="SMA50 ÷ SMA200 · >1 = bullish structure">
        SMA50/200 ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('dist_from_52w_high_pct', {
    header: () => (
      <span className="col-tip" title="Distance from the 52-week high · 0% = at the high">
        52W Dist ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('iv_hv_ratio', {
    header: () => (
      <span className="col-tip" title="Implied Volatility ÷ 30-day Historical Volatility · >1.0 = options priced above realized moves">
        IV/HV ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('rsi', {
    header: () => (
      <span className="col-tip" title="RSI(14) momentum oscillator · >70 overbought · <30 oversold">
        RSI(14) ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('vol_support_126_1', {
    header: () => (
      <span className="col-tip" title="Volume Profile support levels (6M lookback)">
        Vol Support 6M ⓘ
      </span>
    ),
    cell: () => null,
    enableSorting: false,
  }),
  col.accessor('earnings_date', { header: 'Earnings', cell: () => null }),
  // Hidden sort key
  col.accessor('best_roc', { header: () => null, cell: () => null }),
]

function groupResults(results: EmRankResult[]): GroupedEmRankResult[] {
  const map = new Map<string, GroupedEmRankResult>()
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
        iv_hv_ratio: r.iv_hv_ratio,
        best_roc: 0,
        using_hv_fallback: false,
        expirations: [],
      })
    }
    const group = map.get(r.symbol)!
    if (r.earnings_within_dte) group.earnings_within_dte = true
    if (r.using_hv_fallback) group.using_hv_fallback = true
    if (r.iv_hv_ratio != null && group.iv_hv_ratio == null) group.iv_hv_ratio = r.iv_hv_ratio
    group.expirations.push({
      dte: r.dte,
      expiration: r.expiration,
      earnings_within_dte: r.earnings_within_dte,
      expected_move: r.expected_move,
      chain_median_oi: r.chain_median_oi,
      strikes: r.strikes,
      best_roc: r.best_roc,
      using_hv_fallback: r.using_hv_fallback,
    })
  }
  for (const g of map.values()) {
    g.expirations.sort((a, b) => a.dte - b.dte)
    g.best_roc = Math.max(...g.expirations.map(e => e.best_roc))
  }
  // Preserve server order (already sorted by ROC desc from backend)
  return [...map.values()]
}

interface Props {
  data: EmRankResult[]
}

export function EmRankTable({ data }: Props) {
  const groupedData = useMemo(() => groupResults(data), [data])
  const [sorting, setSorting] = useState<SortingState>([{ id: 'best_roc', desc: true }])
  const [strikeExpanded, setStrikeExpanded] = useState<Set<string>>(new Set())
  const [staleDismissed, setStaleDismissed] = useState(false)

  const anyStale = groupedData.some(
    r => r.using_hv_fallback || r.expirations.some(e => e.strikes.some(s => s.iv_fallback))
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
    state: { sorting, columnVisibility: { best_roc: false } },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  if (groupedData.length === 0) return null

  const fmtSpread = (v: number | null) => {
    if (v == null) return <span className="dim">—</span>
    const cls = v > 10 ? 'spread-wide' : v > 5 ? 'spread-ok' : 'spread-tight'
    return <span className={cls}>{v.toFixed(1)}%</span>
  }

  const rocFmt = (roc: number | null, highlight = false) => {
    if (roc == null) return <span className="dim">—</span>
    const cls =
      roc >= 12 ? 'score-strong'
      : roc >= 8 ? 'score-good'
      : roc >= 4 ? 'score-caution'
      : 'score-warn'
    return (
      <span
        className={cls}
        style={highlight ? { fontWeight: 800, fontSize: '15px' } : {}}
        title={`Annualized ROC = (mid / (strike − mid)) × (365/DTE) × 100`}
      >
        {roc.toFixed(1)}%
      </span>
    )
  }

  return (
    <div className="table-wrapper">
      {anyStale && !staleDismissed && (
        <div className="stale-banner">
          <span>⚠ Some delta values are HV-estimated — bid/ask = 0 on those strikes (illiquid or pre-open quotes).</span>
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
                <span className="col-tip" title="Days to Expiration">DTE ⓘ</span>
              </th>
              <th>
                <span className="col-tip" title="Expected Move = price × HV(30d) × √(DTE/365) · 1σ dollar range · EM strike is just below the lower bound">
                  Exp. Move ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Strike price · EM = strike just below 1σ lower bound · ▼ shows 2 strikes inside the EM boundary">
                  Strike ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Option mid-price (bid+ask)/2 · falls back to last-traded if bid/ask = 0">
                  Mid ⓘ
                </span>
              </th>
              <th>
                <span className="col-tip" title="Black-Scholes put delta">Delta ⓘ</span>
              </th>
              <th>
                <span className="col-tip" title="(Ask − Bid) / Mid × 100">Spread% ⓘ</span>
              </th>
              <th>
                <span className="col-tip" title="Open Interest (or Volume when market open) at this strike">OI/Vol ⓘ</span>
              </th>
              <th>
                <span className="col-tip" title="(Mid / (Strike − Mid)) × (365 / DTE) × 100 · Annualized ROC on capital tied up">
                  ROC% ⓘ
                </span>
              </th>
            </tr>
          ))}
        </thead>

        {table.getRowModel().rows.map(row => {
          const r = row.original

          // Total <tr> count for rowSpan (main rows + expanded alt rows)
          const totalRows = r.expirations.reduce((sum, exp) => {
            const expKey = `${r.symbol}-${exp.expiration}`
            const altCount = strikeExpanded.has(expKey)
              ? exp.strikes.filter(s => !s.is_em_strike).length
              : 0
            return sum + 1 + altCount
          }, 0)

          const rows: ReactElement[] = []
          let absRowIdx = 0

          for (const [expIdx, exp] of r.expirations.entries()) {
            const key = `${r.symbol}-${exp.expiration}`
            const showAlts = strikeExpanded.has(key)
            if (!exp.strikes?.length) continue
            const emStrike = exp.strikes.find(s => s.is_em_strike) ?? exp.strikes[0]
            const altStrikes = exp.strikes.filter(s => !s.is_em_strike)
            const dteCellRows = 1 + (showAlts ? altStrikes.length : 0)
            const isFirstRow = absRowIdx === 0

            rows.push(
              <tr key={`${expIdx}-best`} className={isFirstRow ? 'first-exp-row' : 'sub-exp-row'}>

                {/* Ticker-level cells — only on absolute first row */}
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
                      : <span>{r.sma_ratio.toFixed(4)}</span>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {isNaN(r.dist_from_52w_high_pct)
                      ? <span className="dim">—</span>
                      : <span>{r.dist_from_52w_high_pct.toFixed(1)}%</span>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.iv_hv_ratio == null
                      ? <span className="dim">—</span>
                      : <span>{r.iv_hv_ratio.toFixed(2)}×</span>
                    }
                  </td>
                  <td rowSpan={totalRows}>
                    {r.rsi == null || isNaN(r.rsi)
                      ? <span className="dim">—</span>
                      : <span>{r.rsi.toFixed(1)}</span>
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

                {/* DTE — spans main + alt strike rows for this expiration */}
                <td className="dte-cell" rowSpan={dteCellRows}>
                  <span className="dte-num">{exp.dte}</span><br />
                  <span className="expiry-date">{exp.expiration}</span>
                  {exp.earnings_within_dte && <span className="earnings-warn"> ⚠</span>}
                  <div className="oi-badge">OI: {exp.chain_median_oi > 0 ? (exp.chain_median_oi >= 1000 ? (exp.chain_median_oi / 1000).toFixed(1) + 'k' : Math.round(exp.chain_median_oi)) : <span className="dim">—</span>}</div>
                </td>

                {/* Expected Move — same rowSpan as DTE */}
                <td className="em-cell" rowSpan={dteCellRows}>
                  {exp.expected_move > 0
                    ? <>
                        <span className="em-range">±${exp.expected_move.toFixed(2)}</span><br />
                        <span className="em-floor" title="1σ lower bound — EM strike is just below this">↓ {(r.price - exp.expected_move).toFixed(2)}</span>
                      </>
                    : <span className="dim">—</span>
                  }
                </td>

                {/* EM strike row */}
                <td className="strike-cell best-strike">
                  <span className="strike-price">{fmt2(emStrike.strike)}</span>
                  <span className="strike-fall"> {((emStrike.strike - r.price) / r.price * 100).toFixed(1)}%</span>
                  <span style={{ fontSize: '10px', color: '#60a5fa', display: 'block' }}>EM↓</span>
                  {altStrikes.length > 0 && (
                    <button className="strike-toggle" onClick={() => toggleStrikes(key)}>
                      {showAlts ? '▲ hide' : `▼ ${altStrikes.length} more`}
                    </button>
                  )}
                </td>
                <td className="prem-cell">${emStrike.mid.toFixed(2)}</td>
                <td>
                  <span>{fmtDelta(emStrike.delta)}</span>
                  {emStrike.iv_fallback && <span className="iv-fallback-tag" title="Delta estimated from HV — market closed">~HV</span>}
                </td>
                <td>{fmtSpread(emStrike.spread_pct)}</td>
                <td>
                  {emStrike.oi_vol >= 1000
                    ? (emStrike.oi_vol / 1000).toFixed(1) + 'k'
                    : emStrike.oi_vol}
                </td>
                <td>{rocFmt(emStrike.roc_annualized, true)}</td>
              </tr>
            )
            absRowIdx++

            // ── Alt strike rows ──────────────────────────────────────────
            if (showAlts) {
              for (const [si, s] of altStrikes.entries()) {
                rows.push(
                  <tr key={`${expIdx}-alt-${si}`} className="alt-strike-row">
                    <td className="strike-cell">
                      <span className="strike-price">{fmt2(s.strike)}</span>
                      <span className="strike-fall"> {((s.strike - r.price) / r.price * 100).toFixed(1)}%</span>
                    </td>
                    <td className="prem-cell">${s.mid.toFixed(2)}</td>
                    <td>
                      <span>{fmtDelta(s.delta)}</span>
                      {s.iv_fallback && <span className="iv-fallback-tag" title="Delta estimated from HV — market closed">~HV</span>}
                    </td>
                    <td>{fmtSpread(s.spread_pct)}</td>
                    <td>
                      {s.oi_vol >= 1000
                        ? (s.oi_vol / 1000).toFixed(1) + 'k'
                        : s.oi_vol}
                    </td>
                    <td>{rocFmt(s.roc_annualized)}</td>
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
        EM strike = highest put strike ≤ (price − 1σ Expected Move). Alternates are the next 2 strikes inside the EM boundary. Ranked by annualized ROC at the EM strike.
      </p>
    </div>
  )
}

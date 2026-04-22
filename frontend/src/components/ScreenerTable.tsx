import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from '@tanstack/react-table'
import { useState } from 'react'
import type { ScreenerResult } from '../types/screener'

const col = createColumnHelper<ScreenerResult>()

function fmt2(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}
function fmt4(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(4)
}
function fmtPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2) + '%'
}
function fmtAnn(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(1) + '%'
}
function fmtDelta(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(3)
}
function fmtMoney(n: number | null | undefined): string {
  if (n == null) return '—'
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

const COLUMNS = [
  col.accessor('symbol', {
    header: 'Symbol',
    cell: info => <strong>{info.getValue()}</strong>,
  }),
  col.accessor('csp_score', {
    header: () => (
      <span className="col-tip" title="CSP score 0-100: IV Rank(25) + Ann.Return(20) + SMA trend(20) + RSI zone(15) + Delta(10) + Spread%(10) − Earnings(−15)">
        Score ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const cls = v >= 70 ? 'positive' : v >= 45 ? 'rsi-ok' : 'negative'
      return <span className={cls} style={{ fontWeight: 700, fontSize: '15px' }}>{v.toFixed(0)}</span>
    },
  }),
  col.accessor('price', {
    header: 'Price',
    cell: info => fmt2(info.getValue()),
  }),
  col.accessor('bb_lower', {
    header: () => (
      <span className="col-tip" title="Bollinger Bands (20, 2σ)  ·  Upper / Middle / Lower">
        BB Bands ⓘ
      </span>
    ),
    cell: info => {
      const row = info.row.original
      return (
        <span className="bb-bands">
          <span className="bb-upper">{fmt2(row.bb_upper)}</span>
          <span className="bb-middle">{fmt2(row.bb_middle)}</span>
          <span className="bb-lower">{fmt2(row.bb_lower)}</span>
        </span>
      )
    },
  }),
  col.accessor('sma_ratio', {
    header: () => (
      <span className="col-tip" title="SMA50 / SMA200  ·  >1.0 = bullish (50 above 200)  ·  <1.0 = bearish">
        SMA50/200 ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null || isNaN(v)) return <span className="dim">—</span>
      const cls = v >= 1 ? 'positive' : 'negative'
      return <span className={cls}>{v.toFixed(4)}</span>
    },
  }),
  col.accessor('rsi', {
    header: () => (
      <span className="col-tip" title="RSI(14) Wilder-smoothed  ·  >70 overbought  ·  <30 oversold  ·  40–70 ideal for CSP">
        RSI(14) ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null || isNaN(v)) return <span className="dim">—</span>
      const cls = v >= 70 ? 'rsi-high' : v <= 30 ? 'rsi-low' : 'rsi-ok'
      return <span className={cls}>{v.toFixed(1)}</span>
    },
  }),
  col.accessor('iv_rank', {
    header: () => (
      <span className="col-tip" title="IV Rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100  ·  HV-based proxy  ·  High = selling expensive vol">
        IV Rank ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const pct = info.row.original.iv_percentile
      if (v == null) return <span className="dim">N/A</span>
      const cls = v >= 50 ? 'badge badge-green' : v >= 30 ? 'badge badge-yellow' : 'badge badge-gray'
      return (
        <span>
          <span className={cls}>{v.toFixed(0)}</span><br />
          <span className="expiry-date">P:{pct != null ? pct.toFixed(0) : '—'}</span>
        </span>
      )
    },
  }),
  col.accessor('earnings_date', {
    header: 'Earnings',
    cell: info => {
      const row = info.row.original
      if (!row.earnings_date) return <span className="dim">—</span>
      return (
        <span className={row.earnings_within_dte ? 'earnings-warn' : ''}>
          {row.earnings_date}
          {row.earnings_within_dte && ' ⚠'}
        </span>
      )
    },
  }),
  col.accessor('strike', {
    header: () => (
      <span className="col-tip" title="Top: strike ≤ BB Lower  ·  Bottom (dim): strike ≤ BB Middle">
        Strike ⓘ
      </span>
    ),
    cell: info => {
      const row = info.row.original
      const fallPct = ((row.strike - row.price) / row.price) * 100
      const midFallPct = ((row.strike_mid - row.price) / row.price) * 100
      return (
        <span>
          <span className={row.strike_is_fallback ? 'fallback' : ''}>
            {fmt2(row.strike)}{row.strike_is_fallback && ' *'}
            <br />
            <span className="strike-fall">{fallPct.toFixed(1)}%</span>
          </span>
          <br />
          <span className={`dim${row.strike_mid_is_fallback ? ' fallback' : ''}`} title="BB Middle strike">
            {fmt2(row.strike_mid)}{row.strike_mid_is_fallback && ' *'}
            <span className="strike-fall"> {midFallPct.toFixed(1)}%</span>
          </span>
        </span>
      )
    },
  }),
  col.accessor('vol_support_1', {
    header: 'Vol Support',
    cell: info => {
      const row = info.row.original
      const levels = [row.vol_support_1, row.vol_support_2, row.vol_support_3].filter(v => v != null) as number[]
      if (levels.length === 0) return <span className="dim">—</span>
      return (
        <span className="vol-support">
          {levels.map((lvl, i) => {
            const fallPct = ((lvl - row.price) / row.price) * 100
            return (
              <span key={i} className="vol-support-level">
                {fmt2(lvl)}<span className="vol-support-pct">{fallPct.toFixed(1)}%</span>
              </span>
            )
          })}
        </span>
      )
    },
    enableSorting: false,
  }),
  col.accessor('delta', {
    header: 'Delta',
    cell: info => {
      const row = info.row.original
      const inRange = row.delta >= -0.30 && row.delta <= -0.15
      const midInRange = row.delta_mid >= -0.30 && row.delta_mid <= -0.15
      return (
        <span>
          <span className={inRange ? 'delta-ok' : 'delta-warn'}>{fmtDelta(row.delta)}</span>
          <br />
          <span className={`dim ${midInRange ? 'delta-ok' : 'delta-warn'}`}>{fmtDelta(row.delta_mid)}</span>
        </span>
      )
    },
  }),
  col.accessor('bid_ask_spread_pct', {
    header: () => (
      <span className="col-tip" title="(Ask − Bid) / Mid × 100  ·  Lower = tighter market  ·  >10% = illiquid">
        Spread% ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v > 10 ? 'spread-wide' : v > 5 ? 'spread-ok' : 'spread-tight'
      return <span className={cls}>{v.toFixed(1)}%</span>
    },
  }),
  col.accessor('dte', {
    header: 'DTE',
    cell: info => {
      const row = info.row.original
      return (
        <span>
          {info.getValue()}<br />
          <span className="expiry-date">{row.expiration}</span>
        </span>
      )
    },
  }),
  col.accessor('premium', {
    header: 'Premium',
    cell: info => {
      const row = info.row.original
      return (
        <span>
          {fmt2(row.premium)}<br />
          <span className="dim">{fmt2(row.premium_mid)}</span>
        </span>
      )
    },
  }),
  col.accessor('collateral', {
    header: 'Collateral',
    cell: info => fmtMoney(info.getValue()),
  }),
  col.accessor('return_pct', {
    header: 'Return %',
    cell: info => fmtPct(info.getValue()),
  }),
  col.accessor('annualized_return', {
    header: 'Ann. Return',
    cell: info => {
      const row = info.row.original
      return (
        <span>
          {fmtAnn(row.annualized_return)}<br />
          <span className="dim">{fmtAnn(row.annualized_return_mid)}</span>
        </span>
      )
    },
  }),
]

interface Props {
  data: ScreenerResult[]
}

export function ScreenerTable({ data }: Props) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'csp_score', desc: true },
  ])

  const table = useReactTable({
    data,
    columns: COLUMNS,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  if (data.length === 0) return null

  return (
    <div className="table-wrapper">
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
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map(row => {
            const r = row.original
            const rowClass = r.earnings_within_dte
              ? 'row-earnings-warn'
              : ''
            return (
              <tr key={row.id} className={rowClass}>
                {row.getVisibleCells().map(cell => (
                  <td key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="table-note">
        * Strike is a fallback (no put ≤ BB Lower). IV Rank/Percentile = HV-based proxy (252-day window). P: = IV Percentile.
      </p>
    </div>
  )
}

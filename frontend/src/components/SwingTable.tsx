import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from '@tanstack/react-table'
import { Fragment, useState } from 'react'
import type { SwingResult } from '../types/swing'

const col = createColumnHelper<SwingResult>()

function fmt2(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}
function fmt1(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(1)
}
function fmtPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(1) + '%'
}

const SETUP_COLOR: Record<string, string> = {
  breakout: '#60a5fa',
  momentum: '#4ade80',
  reversion: '#fbbf24',
  retest: '#a78bfa',
}

const CONFIDENCE_BADGE: Record<string, { color: string; bg: string; label: string }> = {
  high: { color: '#022c22', bg: '#4ade80', label: 'HIGH' },
  medium: { color: '#1e1b00', bg: '#fbbf24', label: 'MED' },
  speculative: { color: '#1e1b22', bg: '#a78bfa', label: 'SPEC' },
}

function scoreColor(s: number): string {
  if (s >= 75) return '#4ade80'
  if (s >= 65) return '#86efac'
  if (s >= 55) return '#fbbf24'
  if (s >= 45) return '#fb923c'
  return '#f87171'
}

const TRIGGER_DESC: Record<string, string> = {
  break_above: 'when price breaks above the base high',
  pullback_to_ema8: 'on a pullback to the 8-day moving average',
  reclaim_confirm: 'on confirmation of the reclaimed level',
  retest_of: 'on retest of prior resistance as support',
  market_close: 'at today\'s close',
}

function PriceLadder({ stop, entry, target }: { stop: number; entry: number; target: number }) {
  const total = target - stop
  if (total <= 0) return null
  const entryPct = Math.max(5, Math.min(90, ((entry - stop) / total) * 100))
  return (
    <div style={{ position: 'relative', height: 30, marginTop: 4, userSelect: 'none' }}>
      <div style={{
        position: 'absolute', left: 0, width: `${entryPct}%`,
        height: 6, top: 11, background: '#450a0a', borderRadius: '3px 0 0 3px',
      }} />
      <div style={{
        position: 'absolute', left: `${entryPct}%`, width: `${100 - entryPct}%`,
        height: 6, top: 11, background: '#14532d', borderRadius: '0 3px 3px 0',
      }} />
      <div style={{
        position: 'absolute', left: 0, top: 0,
        fontSize: 9, color: '#f87171', lineHeight: '1.2',
      }}>
        ▸ ${stop.toFixed(2)}
      </div>
      <div style={{
        position: 'absolute', left: `${entryPct}%`, top: 0,
        transform: 'translateX(-50%)', fontSize: 9, color: '#94a3b8',
        background: '#0f172a', padding: '0 3px', lineHeight: '1.2',
      }}>
        entry
      </div>
      <div style={{
        position: 'absolute', right: 0, top: 0,
        fontSize: 9, color: '#4ade80', lineHeight: '1.2', textAlign: 'right',
      }}>
        ${target.toFixed(2)} ◂
      </div>
    </div>
  )
}

interface Props {
  data: SwingResult[]
}

export function SwingTable({ data }: Props) {
  const [sorting, setSorting] = useState<SortingState>([{ id: 'swing_score', desc: true }])
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  const columns = [
    col.accessor('symbol', {
      header: 'Symbol',
      cell: info => (
        <span style={{ fontWeight: 600 }}>
          {info.getValue()}
          {info.row.original.earnings_warning && (
            <span title="Earnings within 10 days" style={{ marginLeft: 4, color: '#fbbf24' }}>⚠</span>
          )}
        </span>
      ),
    }),
    col.accessor('price', { header: 'Price', cell: i => `$${fmt2(i.getValue())}` }),
    col.accessor('setup_type', {
      header: () => <span title="Breakout: base consolidation + volume surge. Momentum: aligned EMAs + strong ADX. Reversion: oversold bounce above EMA 200. Retest: prior resistance retested as support.">Setup</span>,
      cell: info => {
        const v = info.getValue()
        const color = SETUP_COLOR[v] ?? '#94a3b8'
        return (
          <span style={{ color, fontWeight: 500, textTransform: 'capitalize' }}>
            {v || '—'}
          </span>
        )
      },
    }),
    col.accessor('swing_score', {
      header: () => (
        <span title="Composite swing score 0–100. R:R earns up to 40 pts, setup quality up to 30, trend context up to 20, institutional signals up to 10. Then multiplied by regime, earnings proximity, and chase-entry penalty.">
          Score
        </span>
      ),
      cell: info => {
        const v = info.getValue()
        const bd = info.row.original.breakdown
        return (
          <span style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {/* score number + bar */}
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ color: scoreColor(v), fontWeight: 700, minWidth: 34, textAlign: 'right' }}>
                {v.toFixed(1)}
              </span>
              <span style={{
                display: 'inline-block', width: 44, height: 5,
                background: '#1e2235', borderRadius: 3, overflow: 'hidden',
              }}>
                <span style={{
                  display: 'block', width: `${Math.min(100, v)}%`, height: '100%',
                  background: scoreColor(v), borderRadius: 3,
                }} />
              </span>
            </span>
            {/* bucket pills */}
            {bd && (
              <span style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                {([
                  { label: 'R:R',   pts: bd.rr,            max: 40, color: '#60a5fa' },
                  { label: 'Setup', pts: bd.setup,          max: 30, color: '#4ade80' },
                  { label: 'Ctx',   pts: bd.context,        max: 20, color: '#fbbf24' },
                  { label: 'Inst',  pts: bd.institutional,  max: 10, color: '#a78bfa' },
                ] as const).map(({ label, pts, max, color }) => (
                  <span
                    key={label}
                    title={`${label}: ${pts?.toFixed(1)} / ${max} pts`}
                    style={{
                      fontSize: 11, padding: '1px 4px', borderRadius: 3,
                      background: '#1e2235', color,
                      fontWeight: 600, letterSpacing: 0.2,
                    }}
                  >
                    {pts != null ? `${label} ${pts.toFixed(0)}/${max}` : '—'}
                  </span>
                ))}
              </span>
            )}
          </span>
        )
      },
    }),
    col.accessor('rr', {
      header: () => <span title="Reward-to-Risk: (target − entry) ÷ (entry − stop). Must be ≥2.5 to pass the gate; ≥3.5 earns top points. Higher means more reward per dollar risked.">R:R</span>,
      cell: info => {
        const v = info.getValue()
        const color = v >= 3.5 ? '#4ade80' : v >= 2.75 ? '#86efac' : v >= 2.5 ? '#fbbf24' : '#f87171'
        return <span style={{ color, fontWeight: 600 }}>{fmt1(v)}</span>
      },
    }),    col.display({
      id: 'risk_pct',
      header: () => <span title="Stop-loss distance as a percentage of the entry price. Use this to size your position: shares = (account risk $) \u00f7 (entry price \u00d7 risk %).">% Risk</span>,
      cell: info => {
        const r = info.row.original
        if (!r.entry || !r.stop || r.entry <= 0) return <span>\u2014</span>
        const pct = ((r.entry - r.stop) / r.entry) * 100
        const color = pct > 5 ? '#f87171' : pct > 3 ? '#fb923c' : '#fbbf24'
        return <span style={{ color, fontWeight: 500 }}>{pct.toFixed(1)}%</span>
      },
    }),    col.accessor('entry', {
      header: () => <span title="The structural price where the trade triggers. Place a limit order here — not at the current price. CHASING badge means price has already moved >3% past this level.">Entry · Status</span>,
      cell: info => {
        const row = info.row.original
        const entry = info.getValue() as number
        const trig = row.trigger_kind
        const kindLabel: Record<string, string> = {
          break_above: 'break ↑',
          pullback_to_ema8: 'pull → EMA8',
          reclaim_confirm: 'confirm',
          retest_of: 'retest',
          market_close: 'at close',
        }
        const triggerTitle: Record<string, string> = {
          break_above:      `Wait for price to break above $${fmt2(entry)} on above-average volume. Place a limit order at that level — do not buy before the break.`,
          pullback_to_ema8: `Wait for price to pull back to the 8-day EMA near $${fmt2(entry)}. Enter on the touch or the first green candle off it.`,
          reclaim_confirm:  `Wait for price to reclaim $${fmt2(entry)} and close above it. Enter after the confirmed close, not on the initial poke.`,
          retest_of:        `Price already broke out. Wait for it to come back and retest $${fmt2(entry)} as support, then enter on the bounce.`,
          market_close:     `Enter near today's close around $${fmt2(entry)}. No intraday trigger — just a position into the close.`,
        }
        // Readiness dot: green = within 1% of trigger, red = extended >3%, yellow = waiting
        const nearTrigger = !row.extended && Math.abs((row.price - entry) / entry) <= 0.01
        const dotColor = row.extended ? '#f87171' : nearTrigger ? '#4ade80' : '#fbbf24'
        const dotTitle = row.extended
          ? `Extended — current price ($${fmt2(row.price)}) is >3% past the trigger. Wait for a pullback or skip; your real R:R is already degraded.`
          : nearTrigger
          ? `Near trigger — price ($${fmt2(row.price)}) is within 1% of the entry level. Good zone to place your limit order.`
          : `Not yet triggered — price ($${fmt2(row.price)}) hasn't reached $${fmt2(entry)} yet. Set an alert and wait.`
        return (
          <span>
            <span title={dotTitle} style={{ marginRight: 5, color: dotColor, cursor: 'default', fontSize: 10 }}>●</span>
            <span style={{ fontWeight: 600 }}>${fmt2(entry)}</span>
            {trig && trig in kindLabel && (
              <span
                title={triggerTitle[trig] ?? ''}
                style={{
                  marginLeft: 6,
                  fontSize: 11,
                  color: '#94a3b8',
                  background: '#1e2235',
                  padding: '1px 5px',
                  borderRadius: 3,
                  letterSpacing: 0.3,
                  cursor: 'help',
                }}>
                {kindLabel[trig]}
              </span>
            )}
            {row.extended && (
              <span title="Current price is more than 3% past the trigger — chasing entry"
                style={{
                  marginLeft: 4,
                  fontSize: 11,
                  color: '#fbbf24',
                  background: '#3a2e0a',
                  padding: '1px 5px',
                  borderRadius: 3,
                  fontWeight: 700,
                  letterSpacing: 0.3,
                }}>
                CHASING
              </span>
            )}
          </span>
        )
      },
    }),
    col.accessor('stop', { header: 'Stop', cell: i => (
      <span style={{ color: '#f87171' }}>${fmt2(i.getValue())}</span>
    ) }),
    col.accessor('target', { header: 'Target', cell: i => (
      <span style={{ color: '#4ade80' }}>${fmt2(i.getValue())}</span>
    ) }),
    col.accessor(row => `${row.hold_min_days}–${row.hold_max_days}d`, {
      id: 'hold',
      header: () => <span title="Suggested hold period in trading days. Auto-trimmed when an earnings event falls inside the window.">Hold</span>,
      cell: info => <span style={{ fontSize: 11 }}>{info.getValue()}</span>,
    }),
    col.display({
      id: 'earnings',
      header: () => <span title="Days until the next earnings report. \u226514d → no flag. \u226414d → yellow. \u22647d → orange. Reversion setups are blocked within 7 days; all setups blocked within 1 day.">Earnings</span>,
      cell: info => {
        const r = info.row.original
        const dte = r.days_to_earnings
        if (dte == null || dte < 0) return <span style={{ color: '#475569', fontSize: 11 }}>\u2014</span>
        const color = dte <= 7 ? '#f97316' : dte <= 14 ? '#fbbf24' : '#64748b'
        return <span style={{ color, fontSize: 11, fontWeight: dte <= 14 ? 600 : 400 }}>{dte}d</span>
      },
    }),
  ]

  const table = useReactTable({
    data,
    columns,
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
              {hg.headers.map(h => (
                <th
                  key={h.id}
                  className="sortable"
                  onClick={h.column.getToggleSortingHandler()}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {{ asc: ' ▲', desc: ' ▼' }[h.column.getIsSorted() as string] ?? ''}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map(row => {
            const r = row.original
            const isExpanded = expandedRow === r.symbol
            return (
              <Fragment key={r.symbol}>
                <tr
                  onClick={() => setExpandedRow(isExpanded ? null : r.symbol)}
                  style={{ cursor: 'pointer' }}
                >
                  {row.getVisibleCells().map(c => (
                    <td key={c.id}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
                  ))}
                </tr>
                {isExpanded && (
                  <tr className="sub-exp-row">
                    <td colSpan={columns.length}>
                      {r.extended && (
                        <div style={{
                          margin: '0 12px 8px',
                          padding: '6px 10px',
                          background: '#3a2e0a',
                          border: '1px solid #fbbf24',
                          borderRadius: 4,
                          fontSize: 12,
                          color: '#fbbf24',
                        }}>
                          ⚠ <strong>Chasing</strong> — current price (${fmt2(r.price)}) is more than 3% past
                          the structural trigger (${fmt2(r.entry)}). Wait for a pullback or skip; entering here
                          degrades the real R:R to roughly {(((r.target - r.price) / Math.max(0.01, r.price - r.stop))).toFixed(1)}.
                        </div>
                      )}
                      <div style={{
                        margin: '8px 12px',
                        padding: '10px 14px',
                        background: '#0f172a',
                        border: '1px solid #1e2235',
                        borderRadius: 6,
                        fontSize: 12,
                      }}>
                        <div style={{ fontWeight: 700, fontSize: 11, marginBottom: 6, color: '#64748b', letterSpacing: 0.8, textTransform: 'uppercase' }}>
                          Trade Plan
                        </div>
                        <p style={{ margin: '0 0 8px', lineHeight: 1.75 }}>
                          Enter near{' '}<strong style={{ color: '#f0f4ff' }}>${fmt2(r.entry)}</strong>
                          {r.trigger_kind && TRIGGER_DESC[r.trigger_kind] && (
                            <span style={{ color: '#64748b' }}>{' '}({TRIGGER_DESC[r.trigger_kind]})</span>
                          )}.{' '}
                          Stop at{' '}<strong style={{ color: '#f87171' }}>${fmt2(r.stop)}</strong>
                          <span style={{ color: '#64748b' }}>{' '}(risking ${fmt2(r.risk_per_share)}/share)</span>.{' '}
                          Target{' '}<strong style={{ color: '#4ade80' }}>${fmt2(r.target)}</strong>
                          <span style={{ color: '#64748b' }}>{' '}(reward ${fmt2(r.reward_per_share)}/share — {fmt1(r.rr)}R)</span>.{' '}
                          Hold{' '}<strong>{r.hold_min_days}–{r.hold_max_days} days</strong>.
                        </p>
                        <PriceLadder stop={r.stop} entry={r.entry} target={r.target} />
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, padding: 12 }}>
                        <div>
                          <h4 style={{ margin: '0 0 8px', fontSize: 13 }}>Setup Drivers</h4>
                          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                            {r.drivers.map((d, i) => <li key={i}>{d}</li>)}
                          </ul>
                          {r.narrative && (
                            <>
                              <h4 style={{ margin: '12px 0 4px', fontSize: 13 }}>AI Narrative</h4>
                              <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5 }}>{r.narrative}</p>
                            </>
                          )}
                          {r.risk_note && (
                            <>
                              <h4 style={{ margin: '8px 0 4px', fontSize: 13, color: '#fbbf24' }}>Invalidation</h4>
                              <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5, color: '#fbbf24' }}>{r.risk_note}</p>
                            </>
                          )}
                        </div>
                        <div style={{ maxWidth: 300 }}>
                          <h4 style={{ margin: '0 0 6px', fontSize: 13 }}>Score Breakdown</h4>
                          <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', background: '#1e2235', marginBottom: 4 }}>
                            <div title={`R:R: ${r.breakdown.rr?.toFixed(1)} / 40 pts`}         style={{ width: `${r.breakdown.rr        || 0}%`, background: '#60a5fa', transition: 'width 0.3s' }} />
                            <div title={`Setup: ${r.breakdown.setup?.toFixed(1)} / 30 pts`}     style={{ width: `${r.breakdown.setup      || 0}%`, background: '#4ade80', transition: 'width 0.3s' }} />
                            <div title={`Context: ${r.breakdown.context?.toFixed(1)} / 20 pts`} style={{ width: `${r.breakdown.context    || 0}%`, background: '#fbbf24', transition: 'width 0.3s' }} />
                            <div title={`Inst: ${r.breakdown.institutional?.toFixed(1)} / 10 pts`} style={{ width: `${r.breakdown.institutional || 0}%`, background: '#a78bfa', transition: 'width 0.3s' }} />
                          </div>
                          <div style={{ display: 'flex', gap: 10, fontSize: 11, color: '#64748b', marginBottom: 8 }}>
                            <span><span style={{ color: '#60a5fa' }}>■</span> R:R /40</span>
                            <span><span style={{ color: '#4ade80' }}>■</span> Setup /30</span>
                            <span><span style={{ color: '#fbbf24' }}>■</span> Context /20</span>
                            <span><span style={{ color: '#a78bfa' }}>■</span> Inst /10</span>
                          </div>
                          <table style={{ fontSize: 11, width: '100%' }}>
                            <tbody>
                              <tr><td>R:R</td><td style={{ textAlign: 'right' }}>{r.breakdown.rr?.toFixed(1)} / 40</td></tr>
                              <tr><td>Setup</td><td style={{ textAlign: 'right' }}>{r.breakdown.setup?.toFixed(1)} / 30</td></tr>
                              <tr><td>Context (ADX + A/D)</td><td style={{ textAlign: 'right' }}>{r.breakdown.context?.toFixed(1)} / 20</td></tr>
                              <tr><td>Institutional</td><td style={{ textAlign: 'right' }}>{r.breakdown.institutional?.toFixed(1)} / 10</td></tr>
                              <tr style={{ borderTop: '1px solid #334155' }}>
                                <td style={{ paddingTop: 4 }}>Raw subtotal</td>
                                <td style={{ textAlign: 'right', paddingTop: 4 }}>{r.raw_score.toFixed(1)} / 100</td>
                              </tr>
                            </tbody>
                          </table>
                          {r.multipliers && Object.keys(r.multipliers).length > 0 && (
                            <>
                              <h4 style={{ margin: '12px 0 4px', fontSize: 13 }}>Multipliers</h4>
                              <table style={{ fontSize: 11, width: '100%' }}>
                                <tbody>
                                  <tr>
                                    <td>Regime ({r.regime_label || 'baseline'})</td>
                                    <td style={{ textAlign: 'right', color: r.multipliers.regime < 1 ? '#fbbf24' : '#94a3b8' }}>
                                      ×{r.multipliers.regime?.toFixed(2)}
                                    </td>
                                  </tr>
                                  <tr>
                                    <td>
                                      Earnings
                                      {r.days_to_earnings != null && ` (${r.days_to_earnings}d)`}
                                    </td>
                                    <td style={{ textAlign: 'right', color: r.multipliers.earnings < 1 ? '#fbbf24' : '#94a3b8' }}>
                                      ×{r.multipliers.earnings?.toFixed(2)}
                                    </td>
                                  </tr>
                                  <tr>
                                    <td>Extended {r.extended ? '(chasing)' : ''}</td>
                                    <td style={{ textAlign: 'right', color: r.multipliers.extended < 1 ? '#fbbf24' : '#94a3b8' }}>
                                      ×{r.multipliers.extended?.toFixed(2)}
                                    </td>
                                  </tr>
                                  <tr style={{ borderTop: '1px solid #334155' }}>
                                    <td style={{ paddingTop: 4, fontWeight: 600 }}>Final score</td>
                                    <td style={{ textAlign: 'right', paddingTop: 4, fontWeight: 700 }}>
                                      {r.swing_score.toFixed(1)} / 100
                                    </td>
                                  </tr>
                                  {r.rr_gate > 0 && (
                                    <tr>
                                      <td style={{ color: '#64748b' }}>R:R gate</td>
                                      <td style={{ textAlign: 'right', color: '#64748b' }}>≥ {r.rr_gate.toFixed(1)}</td>
                                    </tr>
                                  )}
                                  {r.forced_short_hold && (
                                    <tr>
                                      <td colSpan={2} style={{ color: '#fbbf24', fontSize: 10, paddingTop: 4 }}>
                                        ⚠ Hold window trimmed to avoid earnings
                                      </td>
                                    </tr>
                                  )}
                                </tbody>
                              </table>
                            </>
                          )}
                          <h4 style={{ margin: '12px 0 4px', fontSize: 13 }}>Setup Scores</h4>
                          <table style={{ fontSize: 11, width: '100%' }}>
                            <tbody>
                              {Object.entries(r.setup_scores).map(([k, v]) => (
                                <tr key={k}>
                                  <td style={{ textTransform: 'capitalize' }}>{k}</td>
                                  <td style={{ textAlign: 'right', fontWeight: k === r.setup_type ? 600 : 400 }}>
                                    {v.toFixed(0)}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          <h4 style={{ margin: '12px 0 4px', fontSize: 13 }}>Signals</h4>
                          <div style={{ fontSize: 11, lineHeight: 1.6 }}>
                            <div>RSI: {fmt1(r.rsi)} · ADX: {fmt1(r.adx)} · ATR: {fmt2(r.atr14)}</div>
                            <div>RS vs SPY: {fmt2(r.rs_vs_spy)} · EMA align: {r.ema_alignment_score ?? '—'}/9</div>
                            <div>A/D slope: {fmtPct(r.ad_line_slope_pct)} · Inst own: {fmtPct(r.institutional_ownership_pct)}</div>
                            {r.consolidation_days && r.consolidation_days > 0 && (
                              <div>Base: {r.consolidation_days}d / {((r.consolidation_range_pct ?? 0) * 100).toFixed(1)}% range</div>
                            )}
                            {r.volume_surge_ratio != null && (
                              <div>Volume: {fmt2(r.volume_surge_ratio)}× avg</div>
                            )}
                            {r.earnings_date && (
                              <div style={{ color: r.earnings_warning ? '#fbbf24' : 'inherit' }}>
                                Earnings: {r.earnings_date}{r.earnings_warning ? ' ⚠ within 10d' : ''}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

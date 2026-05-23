import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from '@tanstack/react-table'
import { Fragment, useEffect, useState } from 'react'
import type { SwingResult, SwingScorerVersion } from '../types/swing'

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

function scoreColor(s: number): string {
  if (s >= 80) return '#4ade80'
  if (s >= 65) return '#86efac'
  if (s >= 50) return '#fbbf24'
  if (s >= 35) return '#fb923c'
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
  gatesBypassed?: boolean
  scorerVersion?: SwingScorerVersion
}

export function SwingTable({ data, gatesBypassed = false, scorerVersion = 'v3' }: Props) {
  const [sorting, setSorting] = useState<SortingState>([{ id: 'composite_score', desc: true }])
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  // When the user flips the scorer toggle, keep composite as default sort
  useEffect(() => {
    setSorting([{ id: 'composite_score', desc: true }])
  }, [scorerVersion])

  function readinessRank(row: SwingResult): number {
    if (row.extended) return 2
    const nearTrigger = Math.abs((row.price - row.entry) / row.entry) <= 0.01
    return nearTrigger ? 0 : 1
  }

  const columns = [
    // ── 1. STOCK — ticker + price + badges ────────────────────────────────────
    col.display({
      size: 160,
      id: 'symbol',
      header: 'Stock',
      cell: info => {
        const r = info.row.original
        return (
          <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ fontWeight: 700, fontSize: 14 }}>{r.symbol}</span>
              {r.earnings_warning && (
                <span title="Earnings within 10 days — score already reduced" style={{ color: '#fbbf24', fontSize: 12 }}>⚠</span>
              )}
              {gatesBypassed && (
                <span title="Custom — quality filters off" style={{
                  padding: '1px 4px', background: '#1e1b4b', color: '#a5b4fc',
                  border: '1px solid #4338ca', borderRadius: 3, fontSize: 9, fontWeight: 700,
                }}>CUSTOM</span>
              )}
            </span>
            <span style={{ fontSize: 12, color: '#94a3b8' }}>${fmt2(r.price)}</span>
          </span>
        )
      },
    }),

    // ── 2. SETUP — pattern + score stacked ───────────────────────────────────
    col.accessor(row => row.composite_score ?? 0, {
      size: 220,
      id: 'composite_score',
      header: 'Setup & Score',
      cell: info => {
        const score = info.getValue() as number
        const r = info.row.original
        const v = r.setup_type ?? ''
        const DISPLAY: Record<string, string> = {
          breakout: 'Breakout', momentum: 'Momentum', reversion: 'Bounce', retest: 'Retest',
        }
        return (
          <span style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {/* Pattern pill */}
            <span style={{
              alignSelf: 'flex-start',
              color: SETUP_COLOR[v] ?? '#94a3b8',
              background: v === 'breakout' ? '#0f2744' : v === 'momentum' ? '#0f2d1a' : v === 'reversion' ? '#2d2200' : '#1e1040',
              padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700,
              border: `1px solid ${(SETUP_COLOR[v] ?? '#94a3b8')}30`,
            }}>
              {DISPLAY[v] ?? v}
            </span>
            {/* Score row */}
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ color: scoreColor(score), fontWeight: 700, fontSize: 16, minWidth: 28 }}>{score}</span>
              <span style={{ display: 'inline-block', width: 48, height: 5, background: '#1e2235', borderRadius: 3, overflow: 'hidden' }}>
                <span style={{ display: 'block', width: `${Math.min(100, score)}%`, height: '100%', background: scoreColor(score), borderRadius: 3 }} />
              </span>
            </span>
          </span>
        )
      },
    }),

    // ── 3. SIGNALS — momentum + price position from breakdown pts (always populated) ──
    col.display({
      size: 180,
      id: 'signals',
      header: () => <span title="Momentum: is buying pressure rising? Position: where is price in its recent range? Both come from the scoring model — higher bars = stronger signal.">Signals</span>,
      cell: info => {
        const r = info.row.original
        const macdPts = (r.breakdown?.macd ?? 0) as number
        const bbPts   = (r.breakdown?.bb ?? 0) as number
        const mColor = macdPts >= 18 ? '#4ade80' : macdPts >= 10 ? '#86efac' : macdPts >= 4 ? '#fbbf24' : '#475569'
        const bColor = bbPts >= 15 ? '#4ade80' : bbPts >= 8 ? '#86efac' : bbPts >= 4 ? '#fbbf24' : '#475569'
        const mLabel = macdPts >= 18 ? 'Strong ↑' : macdPts >= 10 ? 'Rising ↑' : macdPts >= 4 ? 'Building' : 'Flat'
        const bLabel = bbPts >= 15 ? 'Near high' : bbPts >= 8 ? 'Upper half' : bbPts >= 4 ? 'Midrange' : 'Near low'
        const raw_macd = r.macd_hist_val
        const raw_bb = r.bb_position_val
        return (
          <span style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              <span style={{ fontSize: 10, color: '#475569' }}>Momentum</span>
              <span style={{ fontSize: 12, color: mColor, fontWeight: 600 }}>
                {mLabel}
                {raw_macd != null && <span style={{ color: '#475569', fontWeight: 400 }}> ({raw_macd >= 0 ? '+' : ''}{raw_macd.toFixed(2)})</span>}
              </span>
            </span>
            <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              <span style={{ fontSize: 10, color: '#475569' }}>Position</span>
              <span style={{ fontSize: 12, color: bColor, fontWeight: 600 }}>
                {bLabel}
                {raw_bb != null && <span style={{ color: '#475569', fontWeight: 400 }}> ({(raw_bb * 100).toFixed(0)}%)</span>}
              </span>
            </span>
          </span>
        )
      },
    }),

    // ── 4. TRADE PLAN — entry + stop + target + R:R as one execution unit ─────
    col.accessor('entry', {
      size: 260,
      header: () => <span title="The complete trade plan: when to enter, where to cut the loss (stop), and what to aim for (target). R:R = reward ÷ risk.">Trade Plan</span>,
      sortingFn: (a, b) => readinessRank(a.original) - readinessRank(b.original),
      cell: info => {
        const row = info.row.original
        const entry = info.getValue() as number
        const trig = row.trigger_kind
        const kindLabel: Record<string, string> = {
          break_above: 'break ↑', pullback_to_ema8: '↩ EMA8',
          reclaim_confirm: 'confirm', retest_of: 'retest', market_close: 'at close',
        }
        const triggerTitle: Record<string, string> = {
          break_above:      `Wait for price to break above $${fmt2(entry)} on above-average volume.`,
          pullback_to_ema8: `Wait for price to pull back to the 8-day EMA near $${fmt2(entry)}.`,
          reclaim_confirm:  `Wait for price to reclaim $${fmt2(entry)} and close above it.`,
          retest_of:        `Wait for price to retest $${fmt2(entry)} as support, then enter on the bounce.`,
          market_close:     `Enter near today's close around $${fmt2(entry)}.`,
        }
        const nearTrigger = !row.extended && Math.abs((row.price - entry) / entry) <= 0.01
        const dotColor = row.extended ? '#f87171' : nearTrigger ? '#4ade80' : '#fbbf24'
        const dotTitle = row.extended ? `Extended — price is >3% past trigger` : nearTrigger ? `Near trigger — good zone to place order` : `Not yet — price hasn't reached entry level`
        const stopPct = ((entry - row.stop) / entry * 100)
        const rrColor = row.rr >= 3.5 ? '#4ade80' : row.rr >= 2.75 ? '#86efac' : '#fbbf24'
        return (
          <span style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 180 }}>
            {/* Entry row */}
            <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span title={dotTitle} style={{ color: dotColor, fontSize: 9, cursor: 'default' }}>●</span>
              <span style={{ fontWeight: 600, fontSize: 13 }}>Enter ${fmt2(entry)}</span>
              {trig && trig in kindLabel && (
                <span title={triggerTitle[trig] ?? ''} style={{ fontSize: 10, color: '#94a3b8', background: '#1e2235', padding: '1px 5px', borderRadius: 3, cursor: 'help' }}>
                  {kindLabel[trig]}
                </span>
              )}
              {row.extended && (
                <span title="Price moved past the ideal entry — wait for pullback" style={{ fontSize: 10, color: '#fbbf24', background: '#3a2e0a', padding: '1px 5px', borderRadius: 3, fontWeight: 700 }}>
                  LATE
                </span>
              )}
            </span>
            {/* Stop row */}
            <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ color: '#475569', fontSize: 10, minWidth: 8 }}>▼</span>
              <span style={{ color: '#f87171', fontWeight: 600 }}>Stop ${fmt2(row.stop)}</span>
              <span style={{ fontSize: 10, color: '#64748b' }}>−{stopPct.toFixed(1)}%</span>
            </span>
            {/* Target row */}
            <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ color: '#475569', fontSize: 10, minWidth: 8 }}>→</span>
              <span style={{ color: '#4ade80', fontWeight: 600 }}>Target ${fmt2(row.target)}</span>
              <span style={{ fontSize: 11, color: rrColor, fontWeight: 700 }}>{fmt1(row.rr)}R</span>
            </span>
          </span>
        )
      },
    }),

    // ── 5. HOLD ───────────────────────────────────────────────────────────────
    col.accessor(row => `${row.hold_min_days}–${row.hold_max_days}d`, {
      size: 90,
      id: 'hold',
      header: () => <span title="How many trading days to hold the position before taking profit or reassessing.">Hold</span>,
      cell: info => <span style={{ fontSize: 11, color: '#94a3b8' }}>{info.getValue()}</span>,
    }),

    // ── 6. EARNINGS ───────────────────────────────────────────────────────────
    col.display({
      size: 100,
      id: 'earnings',
      header: () => <span title="Days until next earnings report. Within 10 days = elevated risk. Score is already reduced for nearby earnings.">Earnings</span>,
      cell: info => {
        const r = info.row.original
        const dte = r.days_to_earnings
        if (dte == null || dte < 0) return <span style={{ color: '#475569', fontSize: 11 }}>—</span>
        const color = dte <= 7 ? '#f97316' : dte <= 14 ? '#fbbf24' : '#64748b'
        return <span style={{ color, fontSize: 11, fontWeight: dte <= 14 ? 600 : 400 }}>{dte <= 7 ? `${dte}d ⚠` : `${dte}d`}</span>
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
      <table className="screener-table" style={{ tableLayout: 'fixed' }}>
        <thead>
          {table.getHeaderGroups().map(hg => (
            <tr key={hg.id}>
              {hg.headers.map(h => (
                <th
                  key={h.id}
                  className="sortable"
                  style={{ width: h.column.getSize() }}
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
                  data-setup={r.setup_type}
                  onClick={() => setExpandedRow(isExpanded ? null : r.symbol)}
                  style={{ cursor: 'pointer' }}
                >
                  {row.getVisibleCells().map(c => (
                    <td key={c.id} style={{ width: c.column.getSize() }}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
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
                        <div style={{ maxWidth: 320 }}>
                          {scorerVersion === 'v3' && (
                            <>
                              <h4 style={{ margin: '0 0 6px', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
                                v3 Calibrated Score
                                <span style={{ fontSize: 10, color: '#94a3b8', fontWeight: 400 }}>
                                  P(target) = {((r.p_target ?? 0) * 100).toFixed(1)}%
                                </span>
                              </h4>
                              <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 6, lineHeight: 1.5 }}>
                                Lasso-trained probability the price reaches the target before the stop.
                                Top features below show which signals pushed this trade above or below the
                                training-set mean.
                              </div>
                              {r.lasso_top_features && r.lasso_top_features.length > 0 ? (
                                <table style={{ fontSize: 11, width: '100%', marginBottom: 10 }}>
                                  <thead>
                                    <tr style={{ color: '#64748b' }}>
                                      <th style={{ textAlign: 'left', fontWeight: 500 }}>Feature</th>
                                      <th style={{ textAlign: 'right', fontWeight: 500 }}>Value</th>
                                      <th style={{ textAlign: 'right', fontWeight: 500 }} title="Z-score vs training-set mean">σ</th>
                                      <th style={{ textAlign: 'right', fontWeight: 500 }} title="log-odds contribution = coef \u00d7 z-score">Δ</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {r.lasso_top_features.map(f => {
                                      const contribColor = f.contribution > 0 ? '#4ade80' : '#f87171'
                                      return (
                                        <tr key={f.name}>
                                          <td style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10.5 }}>{f.name}</td>
                                          <td style={{ textAlign: 'right' }}>{f.value.toFixed(2)}</td>
                                          <td style={{ textAlign: 'right', color: '#94a3b8' }}>{f.std_value >= 0 ? '+' : ''}{f.std_value.toFixed(2)}</td>
                                          <td style={{ textAlign: 'right', color: contribColor, fontWeight: 600 }}>
                                            {f.contribution >= 0 ? '+' : ''}{f.contribution.toFixed(3)}
                                          </td>
                                        </tr>
                                      )
                                    })}
                                  </tbody>
                                </table>
                              ) : (
                                <div style={{ fontSize: 11, color: '#64748b', marginBottom: 10, fontStyle: 'italic' }}>
                                  No contributions available — feature extraction may have failed.
                                </div>
                              )}
                              {r.lasso_missing_features && r.lasso_missing_features.length > 0 && (
                                <div style={{
                                  fontSize: 10, color: '#fbbf24', marginBottom: 10,
                                  padding: '4px 8px', background: '#2d2200', borderRadius: 3,
                                }}>
                                  Missing features (mean-imputed): {r.lasso_missing_features.join(', ')}
                                </div>
                              )}
                              <h4 style={{ margin: '12px 0 6px', fontSize: 13, color: '#64748b' }}>
                                v2 Bucket Breakdown (for reference)
                              </h4>
                            </>
                          )}
                          {scorerVersion !== 'v3' && (
                            <h4 style={{ margin: '0 0 6px', fontSize: 13 }}>Score Breakdown</h4>
                          )}
                          <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', background: '#1e2235', marginBottom: 4 }}>
                            <div title={`R:R: ${r.breakdown.rr?.toFixed(1)} / 40 pts`}   style={{ width: `${((r.breakdown.rr   || 0) / 125 * 100).toFixed(1)}%`, background: '#60a5fa', transition: 'width 0.3s' }} />
                            <div title={`Setup: ${r.breakdown.setup?.toFixed(1)} / 30 pts`} style={{ width: `${((r.breakdown.setup || 0) / 125 * 100).toFixed(1)}%`, background: '#4ade80', transition: 'width 0.3s' }} />
                            <div title={`MACD: ${r.breakdown.macd?.toFixed(1)} / 25 pts`} style={{ width: `${((r.breakdown.macd  || 0) / 125 * 100).toFixed(1)}%`, background: '#fbbf24', transition: 'width 0.3s' }} />
                            <div title={`BB pos: ${r.breakdown.bb?.toFixed(1)} / 20 pts`}  style={{ width: `${((r.breakdown.bb    || 0) / 125 * 100).toFixed(1)}%`, background: '#a78bfa', transition: 'width 0.3s' }} />
                            <div title={`Vol: ${r.breakdown.vol?.toFixed(1)} / 10 pts`}   style={{ width: `${((r.breakdown.vol   || 0) / 125 * 100).toFixed(1)}%`, background: '#f97316', transition: 'width 0.3s' }} />
                          </div>
                          <div style={{ display: 'flex', gap: 10, fontSize: 11, color: '#64748b', marginBottom: 8, flexWrap: 'wrap' }}>
                            <span><span style={{ color: '#60a5fa' }}>■</span> R:R /40</span>
                            <span><span style={{ color: '#4ade80' }}>■</span> Setup /30</span>
                            <span><span style={{ color: '#fbbf24' }}>■</span> MACD /25</span>
                            <span><span style={{ color: '#a78bfa' }}>■</span> BB /20</span>
                            <span><span style={{ color: '#f97316' }}>■</span> Vol /10</span>
                          </div>
                          <table style={{ fontSize: 11, width: '100%' }}>
                            <tbody>
                              <tr><td>R:R</td><td style={{ textAlign: 'right' }}>{r.breakdown.rr?.toFixed(1)} / 40</td></tr>
                              <tr><td>Setup</td><td style={{ textAlign: 'right' }}>{r.breakdown.setup?.toFixed(1)} / 30</td></tr>
                              <tr><td>MACD histogram</td><td style={{ textAlign: 'right' }}>{r.breakdown.macd?.toFixed(1)} / 25</td></tr>
                              <tr><td>BB position</td><td style={{ textAlign: 'right' }}>{r.breakdown.bb?.toFixed(1)} / 20</td></tr>
                              <tr><td>Volume surge</td><td style={{ textAlign: 'right' }}>{r.breakdown.vol?.toFixed(1)} / 10</td></tr>
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
                                    <td>
                                      Earnings
                                      {r.days_to_earnings != null && ` (${r.days_to_earnings}d)`}
                                    </td>
                                    <td style={{ textAlign: 'right', color: (r.multipliers.earnings ?? 1) < 1 ? '#fbbf24' : '#94a3b8' }}>
                                      ×{r.multipliers.earnings?.toFixed(2)}
                                    </td>
                                  </tr>
                                  {r.extended && (
                                    <tr>
                                      <td>Extended (flag only — no penalty)</td>
                                      <td style={{ textAlign: 'right', color: '#94a3b8' }}>×1.00</td>
                                    </tr>
                                  )}
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

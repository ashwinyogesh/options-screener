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

function expectedValuePerShare(row: SwingResult): number | null {
  const p = row.p_target ?? (row.swing_score_v3 != null ? row.swing_score_v3 / 100 : null)
  if (p == null) return null
  if (!Number.isFinite(row.reward_per_share) || !Number.isFinite(row.risk_per_share)) return null
  return (p * row.reward_per_share) - ((1 - p) * row.risk_per_share)
}

const SETUP_COLOR: Record<string, string> = {
  breakout: '#60a5fa',
  momentum: '#4ade80',
  reversion: '#fbbf24',
  retest: '#a78bfa',
}

function scoreColor(s: number): string {
  if (s >= 75) return '#4ade80'
  if (s >= 65) return '#86efac'
  if (s >= 55) return '#fbbf24'
  if (s >= 45) return '#fb923c'
  return '#f87171'
}

// v3 = calibrated probability (0–100). Thresholds match
// backend/services/scoring/swing_lasso.confidence_label:
//   p >= 0.65 → high, >= 0.50 → medium, else speculative.
function scoreColorV3(s: number): string {
  if (s >= 65) return '#4ade80'
  if (s >= 50) return '#fbbf24'
  return '#fb923c'
}

function confidencePillColor(c: string | undefined): { bg: string; fg: string } {
  if (c === 'high') return { bg: '#0f2d1a', fg: '#4ade80' }
  if (c === 'medium') return { bg: '#2d2200', fg: '#fbbf24' }
  return { bg: '#2a1810', fg: '#fb923c' }
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
  const initialSortId = scorerVersion === 'v3' ? 'swing_score_v3' : 'swing_score'
  const [sorting, setSorting] = useState<SortingState>([{ id: initialSortId, desc: true }])
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  // When the user flips the scorer toggle, re-default the sort to the active scorer
  // so the top of the table reflects the rankings users are looking at.
  useEffect(() => {
    setSorting([{ id: scorerVersion === 'v3' ? 'swing_score_v3' : 'swing_score', desc: true }])
  }, [scorerVersion])

  function readinessRank(row: SwingResult): number {
    if (row.extended) return 2
    const nearTrigger = Math.abs((row.price - row.entry) / row.entry) <= 0.01
    return nearTrigger ? 0 : 1
  }

  const columns = [
    col.accessor('symbol', {
      header: 'Symbol',
      cell: info => (
        <span style={{ fontWeight: 600 }}>
          {info.getValue()}
          {info.row.original.earnings_warning && (
            <span title="Earnings within 10 days" style={{ marginLeft: 4, color: '#fbbf24' }}>⚠</span>
          )}
          {gatesBypassed && (
            <span
              title="Custom search — strategy gates bypassed. Results shown regardless of price, ADV, setup score, R:R, or earnings filters."
              style={{
                marginLeft: 5,
                padding: '1px 5px',
                background: '#1e1b4b',
                color: '#a5b4fc',
                border: '1px solid #4338ca',
                borderRadius: 3,
                fontSize: 9,
                fontWeight: 700,
                letterSpacing: 0.4,
                cursor: 'help',
                verticalAlign: 'middle',
              }}
            >
              CUSTOM
            </span>
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
        const bg: Record<string, string> = {
          breakout:  '#0f2744',
          momentum:  '#0f2d1a',
          reversion: '#2d2200',
          retest:    '#1e1040',
        }
        return (
          <span style={{
            color, background: bg[v] ?? '#1e2235',
            padding: '2px 9px', borderRadius: 4,
            fontSize: 11, fontWeight: 700, letterSpacing: 0.4,
            textTransform: 'capitalize', border: `1px solid ${color}30`,
          }}>
            {v || '—'}
          </span>
        )
      },
    }),
    scorerVersion === 'v3'
      ? col.accessor(row => row.swing_score_v3 ?? 0, {
          id: 'swing_score_v3',
          header: () => (
            <span title="Calibrated probability that price reaches target before stop. This is the headline metric for v3.">
              Chance To Hit
            </span>
          ),
          cell: info => {
            const v = info.getValue() as number
            const r = info.row.original
            // If the doc was scored before v3 shipped, it carries neither
            // swing_score_v3 nor p_target. Show "not scored" instead of a
            // misleading 0% / speculative pill.
            const hasV3 = r.swing_score_v3 != null && (r.p_target != null || r.lasso_top_features?.length)
            if (!hasV3) {
              return (
                <span
                  title="This row was scored before v3 shipped. Re-scan (or wait for the next screener-worker run) to populate the calibrated probability."
                  style={{
                    fontSize: 10, color: '#64748b', fontStyle: 'italic',
                    display: 'inline-flex', flexDirection: 'column', gap: 2,
                  }}
                >
                  <span style={{ color: '#94a3b8' }}>v3 pending</span>
                  <span>re-scan needed</span>
                </span>
              )
            }
            const p = r.p_target ?? v / 100
            const pill = confidencePillColor(r.lasso_confidence)
            return (
              <span style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: scoreColorV3(v), fontWeight: 700, minWidth: 34, textAlign: 'right' }}>
                    {(p * 100).toFixed(0)}%
                  </span>
                  <span style={{
                    display: 'inline-block', width: 44, height: 5,
                    background: '#1e2235', borderRadius: 3, overflow: 'hidden',
                  }}>
                    <span style={{
                      display: 'block', width: `${Math.min(100, v)}%`, height: '100%',
                      background: scoreColorV3(v), borderRadius: 3,
                    }} />
                  </span>
                </span>
                <span
                  title={`Calibrated confidence based on P(target). high \u2265 65%, medium \u2265 50%, else speculative.`}
                  style={{
                    fontSize: 10, padding: '1px 5px', borderRadius: 3,
                    background: pill.bg, color: pill.fg,
                    fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase',
                    alignSelf: 'flex-start',
                  }}
                >
                  {r.lasso_confidence ?? 'speculative'}
                </span>
              </span>
            )
          },
        })
      : col.accessor('swing_score', {
      header: () => (
        <span title="Composite swing score 0\u2013100 from setup quality, reward/risk, trend context, and institutional confirmation.">
          Composite Score
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
              <span style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3 }}>
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
    ...(scorerVersion === 'v3'
      ? [
          col.display({
            id: 'ev_per_share',
            header: () => <span title="Expected value per share = P(target) × reward per share − (1 − P(target)) × risk per share.">EV / Share</span>,
            cell: info => {
              const ev = expectedValuePerShare(info.row.original)
              if (ev == null) return <span style={{ color: '#64748b' }}>—</span>
              const color = ev >= 0 ? '#4ade80' : '#f87171'
              return <span style={{ color, fontWeight: 600 }}>${fmt2(ev)}</span>
            },
          }),
        ]
      : []),
    col.accessor('rr', {
      header: () => <span title="Reward-to-Risk: (target − entry) ÷ (entry − stop). Higher means more upside for each dollar risked.">Reward / Risk</span>,
      cell: info => {
        const v = info.getValue()
        const color = v >= 3.5 ? '#4ade80' : v >= 2.75 ? '#86efac' : v >= 2.5 ? '#fbbf24' : '#f87171'
        return <span style={{ color, fontWeight: 600 }}>{fmt1(v)}</span>
      },
    }),    col.display({
      id: 'risk_pct',
      header: () => <span title="Maximum loss from trigger to stop, as a percentage of trigger price.">Max Loss %</span>,
      cell: info => {
        const r = info.row.original
        if (!r.entry || !r.stop || r.entry <= 0) return <span>\u2014</span>
        const pct = ((r.entry - r.stop) / r.entry) * 100
        const color = pct > 5 ? '#f87171' : pct > 3 ? '#fb923c' : '#fbbf24'
        return <span style={{ color, fontWeight: 500 }}>{pct.toFixed(1)}%</span>
      },
    }),    col.accessor('entry', {
      header: () => <span title="Trigger price for the setup. Readiness dot: green = near trigger, yellow = waiting, red = extended/chasing.">Trigger Price</span>,
      sortingFn: (a, b) => readinessRank(a.original) - readinessRank(b.original),
      cell: info => {
        const row = info.row.original
        const entry = info.getValue() as number
        const trig = row.trigger_kind
        const kindLabel: Record<string, string> = {
          break_above: 'break ↑',
          pullback_to_ema8: '↩ EMA8',
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
    col.accessor('stop', { header: 'Stop Loss', cell: i => (
      <span style={{ color: '#f87171' }}>${fmt2(i.getValue())}</span>
    ) }),
    col.accessor('target', { header: 'Take Profit', cell: i => (
      <span style={{ color: '#4ade80' }}>${fmt2(i.getValue())}</span>
    ) }),
    col.accessor(row => `${row.hold_min_days}–${row.hold_max_days}d`, {
      id: 'hold',
      header: () => <span title="Planned holding window. Auto-trimmed when it would cross earnings.">Planned Hold</span>,
      cell: info => <span style={{ fontSize: 11 }}>{info.getValue()}</span>,
    }),
    col.display({
      id: 'earnings',
      header: () => <span title="Days until next earnings report. Lower values increase event risk.">Days To Earnings</span>,
      cell: info => {
        const r = info.row.original
        const dte = r.days_to_earnings
        if (dte == null || dte < 0) return <span style={{ color: '#475569', fontSize: 11 }}>{'—'}</span>
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

  // Detect the "showing v3 but cached docs predate v3" case so we can warn the
  // user instead of silently showing 0% for every row.
  const v3Missing = scorerVersion === 'v3' &&
    data.every(r => r.swing_score_v3 == null || (r.p_target == null && !r.lasso_top_features?.length))

  return (
    <div className="table-wrapper">
      {v3Missing && (
        <div style={{
          margin: '0 0 10px',
          padding: '8px 12px',
          background: '#1e1b4b',
          border: '1px solid #4338ca',
          borderRadius: 4,
          fontSize: 12,
          color: '#cbd5e1',
          lineHeight: 1.5,
        }}>
          <strong style={{ color: '#a5b4fc' }}>v3 not in these results.</strong>{' '}
          The cached scan was produced before the v3 Lasso scorer shipped, so the
          rows below only have legacy v2 scores. Re-run the scan (or wait for the
          next screener-worker run) to populate calibrated P(target) values.
          Switch the toggle to <strong>v2</strong> to see the ranks the cache was sorted by.
        </div>
      )}
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
                  data-setup={r.setup_type}
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

import { useMemo, useState } from 'react'
import type { AcsScore } from '../types/narrative'
import { labelSignal } from '../constants/narrative'
import { StageBadge } from './StageBadge'

function acsScoreClass(acs: number): string {
  if (acs >= 75) return 'score-strong'
  if (acs >= 65) return 'score-good'
  if (acs >= 55) return 'score-caution'
  if (acs >= 45) return 'score-warn'
  return 'score-bad'
}

interface NarrativeTickerTableProps {
  rows: AcsScore[]
  emptyMessage: string
  loading?: boolean
  onSelect?: (ticker: string) => void
}

type SortKey = 'ticker' | 'acs' | 'decay_acs' | 'stage' | 'flags'
type SortDir = 'asc' | 'desc'

interface ColumnDef {
  key: SortKey | null
  label: string
  title?: string
  align?: 'left' | 'right' | 'center'
}

const COLUMNS: ColumnDef[] = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'acs', label: 'Score', title: 'Narrative Score (0\u2013100) with 95% confidence range', align: 'center' },
  { key: 'stage', label: 'Stage', title: 'How early is this narrative? Stages 2\u20133 are the ideal entry window.', align: 'center' },
  {
    key: null,
    label: 'Breakdown',
    title: 'A: daily activity · B: post diversity · C: narrative coherence · D: analytical depth · E: market confirmation (price strength, call skew, institutional buying)',
    align: 'left',
  },
  { key: null, label: 'Dominant signal', title: 'Most common discussion type: direction (Bullish/Bearish) \u00d7 style (Analytical = data-backed; Hype-driven = momentum/FOMO)' },
  { key: 'flags', label: 'Warnings' },
]

function getSortValue(row: AcsScore, key: SortKey): number | string {
  switch (key) {
    case 'ticker':    return row.ticker
    case 'acs':       return row.acs
    case 'decay_acs': return row.decay_acs
    case 'stage':     return row.lifecycle_stage
    case 'flags':     return row.flags.length
  }
}

const FLAG_LABELS: Record<string, string> = {
  gini_high:         'Concentrated posts',
  decelerating_3d:   'Fading momentum',
  late_stage:        'Late stage',
  small_cap:         'Small cap',
  small_cap_haircut: 'Small cap',
  low_unique_authors: 'Few authors',
}

function humanizeFlags(flags: string[]): string {
  if (flags.length === 0) return '\u2014'
  return flags.map((f) => FLAG_LABELS[f] ?? f).join(', ')
}

/** Compact component pill: "25" colored, "0" muted. */
function ComponentPill({ letter, value, title }: { letter: string; value: number; title: string }) {
  const zero = value < 0.05
  return (
    <span className={`acs-pill${zero ? ' acs-pill-zero' : ''}`} title={title}>
      <span className="acs-pill-letter">{letter}</span>
      <span className="acs-pill-value">{value.toFixed(0)}</span>
    </span>
  )
}

export function NarrativeTickerTable({ rows, emptyMessage, loading, onSelect }: NarrativeTickerTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('acs')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const sorted = useMemo(() => {
    const copy = [...rows]
    copy.sort((a, b) => {
      const va = getSortValue(a, sortKey)
      const vb = getSortValue(b, sortKey)
      let cmp: number
      if (typeof va === 'number' && typeof vb === 'number') cmp = va - vb
      else cmp = String(va).localeCompare(String(vb))
      return sortDir === 'asc' ? cmp : -cmp
    })
    return copy
  }, [rows, sortKey, sortDir])

  if (loading && rows.length === 0) {
    return <p className="muted">Loading…</p>
  }

  if (rows.length === 0) {
    return <p className="muted">{emptyMessage}</p>
  }

  const onHeaderClick = (key: SortKey | null) => {
    if (key == null) return
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDir(key === 'ticker' ? 'asc' : 'desc')
    }
  }

  return (
    <div className="table-wrapper">
      <table className="screener-table">
      <thead>
        <tr>
          {COLUMNS.map((col) => {
            const sortable = col.key != null
            const active = col.key === sortKey
            const classes = [sortable ? 'sortable' : ''].filter(Boolean).join(' ') || undefined
            const style: React.CSSProperties = {}
            if (col.align) style.textAlign = col.align
            return (
              <th
                key={col.label}
                className={classes}
                onClick={sortable ? () => onHeaderClick(col.key) : undefined}
                style={Object.keys(style).length ? style : undefined}
                aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
              >
                {col.title ? (
                  <span className="col-tip" title={col.title}>{col.label} ⓘ</span>
                ) : (
                  col.label
                )}
                {active && (sortDir === 'asc' ? ' ↑' : ' ↓')}
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => {
          return (
            <tr
              key={`${row.ticker}-${row.scored_at}`}
              onClick={onSelect ? () => onSelect(row.ticker) : undefined}
              style={onSelect ? { cursor: 'pointer' } : undefined}
            >
              <td className="ticker-cell sticky-col sticky-col-1">
                <strong>{row.ticker}</strong>
              </td>
              <td style={{ textAlign: 'center' }}>
                <div className="acs-cell">
                  <span className={`acs-cell-primary ${acsScoreClass(row.acs)}`}>{row.acs.toFixed(1)}</span>
                  <span className="acs-cell-ci">
                    {row.acs_ci_lower.toFixed(0)}–{row.acs_ci_upper.toFixed(0)}
                  </span>
                </div>
              </td>

              <td style={{ textAlign: 'center' }}>
                <StageBadge stage={row.lifecycle_stage} confidence={row.stage_confidence} />
              </td>
              <td>
                <div className="acs-pills">
                  <ComponentPill letter="A" value={row.components.a_attention_persistence} title="Daily activity score: how consistently it's been discussed over 14 days (max 25)" />
                  <ComponentPill letter="B" value={row.components.b_contributor_quality} title="Post diversity score: many different people posting, not one account dominating (max 20)" />
                  <ComponentPill letter="C" value={row.components.c_narrative_strength} title="Narrative coherence: posts share a common thesis (max 20) — needs hourly detector to run" />
                  <ComponentPill letter="D" value={row.components.d_thesis_quality} title="Analytical depth: fraction of posts that include real research, not just hype (max 20)" />
                  <ComponentPill letter="E" value={row.components.e_market_confirmation} title="Market confirmation: sector-relative price strength · call options skew · institutional buying (max 15)" />
                </div>
              </td>
              <td>{labelSignal(row.dominant_signal)}</td>
              <td className="muted">{humanizeFlags(row.flags)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
    </div>
  )
}

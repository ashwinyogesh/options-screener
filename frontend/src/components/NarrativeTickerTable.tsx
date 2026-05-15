import { useMemo, useState } from 'react'
import type { AcsScore } from '../types/narrative'
import { StageBadge } from './StageBadge'

interface NarrativeTickerTableProps {
  rows: AcsScore[]
  emptyMessage: string
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
  { key: 'acs', label: 'ACS', title: 'Attention Conviction Score with 95% bootstrap CI', align: 'center' },
  { key: 'decay_acs', label: 'Decay', title: 'Time-decayed ACS (λ=0.07/day, half-life ≈10d)', align: 'right' },
  { key: 'stage', label: 'Stage', title: 'Lifecycle stage 1–6 (methodology §4)', align: 'center' },
  {
    key: null,
    label: 'Components',
    title: 'A: attention · B: contributors · C: narrative · D: thesis · E: market (§5.1). E currently 0 (Phase 6.1).',
    align: 'left',
  },
  { key: null, label: 'Signal' },
  { key: 'flags', label: 'Flags' },
]

function getSortValue(row: AcsScore, key: SortKey): number | string {
  switch (key) {
    case 'ticker':
      return row.ticker
    case 'acs':
      return row.acs
    case 'decay_acs':
      return row.decay_acs
    case 'stage':
      return row.lifecycle_stage
    case 'flags':
      return row.flags.length
  }
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

export function NarrativeTickerTable({ rows, emptyMessage, onSelect }: NarrativeTickerTableProps) {
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
    <div className="narrative-table-wrap">
      <table className="narrative-table">
      <thead>
        <tr>
          {COLUMNS.map((col) => {
            const sortable = col.key != null
            const active = col.key === sortKey
            const arrow = active ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''
            const style: React.CSSProperties = {}
            if (sortable) {
              style.cursor = 'pointer'
              style.userSelect = 'none'
            }
            if (col.align) style.textAlign = col.align
            return (
              <th
                key={col.label}
                title={col.title}
                onClick={sortable ? () => onHeaderClick(col.key) : undefined}
                style={style}
                aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
              >
                {col.label}
                {arrow}
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => {
          const decayDelta = row.acs - row.decay_acs
          const decayMuted = Math.abs(decayDelta) < 0.1
          return (
            <tr
              key={`${row.ticker}-${row.scored_at}`}
              onClick={onSelect ? () => onSelect(row.ticker) : undefined}
              style={onSelect ? { cursor: 'pointer' } : undefined}
            >
              <td>
                <strong>{row.ticker}</strong>
              </td>
              <td style={{ textAlign: 'center' }}>
                <div className="acs-cell">
                  <span className="acs-cell-primary">{row.acs.toFixed(1)}</span>
                  <span className="acs-cell-ci">
                    {row.acs_ci_lower.toFixed(0)}–{row.acs_ci_upper.toFixed(0)}
                  </span>
                </div>
              </td>
              <td
                className={decayMuted ? 'muted' : undefined}
                style={{ textAlign: 'right' }}
                title="Time-decayed ACS (λ=0.07/day)"
              >
                {row.decay_acs.toFixed(1)}
              </td>
              <td style={{ textAlign: 'center' }}>
                <StageBadge stage={row.lifecycle_stage} confidence={row.stage_confidence} />
              </td>
              <td>
                <div className="acs-pills">
                  <ComponentPill letter="A" value={row.components.a_attention_persistence} title="Attention persistence (§5.1 A, max 25)" />
                  <ComponentPill letter="B" value={row.components.b_contributor_quality} title="Contributor quality (§5.1 B, max 20)" />
                  <ComponentPill letter="C" value={row.components.c_narrative_strength} title="Narrative strength (§5.1 C, max 20)" />
                  <ComponentPill letter="D" value={row.components.d_thesis_quality} title="Thesis quality (§5.1 D, max 20)" />
                  <ComponentPill letter="E" value={row.components.e_market_confirmation} title="Market confirmation (§5.1 E, max 15) — not yet implemented" />
                </div>
              </td>
              <td>{row.dominant_signal}</td>
              <td className="muted">{row.flags.join(', ') || '—'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
    </div>
  )
}

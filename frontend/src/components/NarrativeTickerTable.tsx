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

/** Returns a direction class based on the dominant signal; null when unknown/mixed. */
function acsDirectionClass(signal: string): 'acs-direction-bull' | 'acs-direction-bear' | null {
  if (signal.startsWith('bull')) return 'acs-direction-bull'
  if (signal.startsWith('bear')) return 'acs-direction-bear'
  return null
}

interface NarrativeTickerTableProps {
  rows: AcsScore[]
  emptyMessage: string
  loading?: boolean
  onSelect?: (ticker: string) => void
  /**
   * ADR-0023 — when true, renders Streak / Trend columns and the
   * New / Sustaining / Fading filter chips above the table. Off for the
   * Top-ACS panel (continuity is most useful for the Emerging view).
   */
  showContinuity?: boolean
}

type SortKey = 'ticker' | 'acs' | 'decay_acs' | 'stage' | 'flags' | 'streak' | 'slope' | 'signal'
type SortDir = 'asc' | 'desc'

interface ColumnDef {
  key: SortKey | null
  label: string
  title?: string
  align?: 'left' | 'right' | 'center'
  continuityOnly?: boolean
}

const COLUMNS: ColumnDef[] = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'acs', label: 'Score', title: 'Narrative Score (0\u2013100) with 95% confidence range. \u25b2 green = bullish conviction (\u226560%); \u25bc red = bearish conviction (\u226440%).', align: 'center' },
  { key: 'stage', label: 'Stage', title: 'How early is this narrative? Stages 2\u20133 are the ideal entry window.', align: 'center' },
  { key: 'streak', label: 'Streak', title: 'Consecutive days in stages 2–3 (entry window) ending today (ADR-0023).', align: 'center', continuityOnly: true },
  { key: 'slope', label: 'Trend', title: '14-day ACS slope \u2014 positive means rising, negative means fading (ADR-0023).', align: 'center', continuityOnly: true },
  {
    key: null,
    label: 'Breakdown',
    title: 'A: daily activity · B: post diversity · C: narrative coherence · D: analytical depth · E: market confirmation (price strength, call skew, institutional buying)',
    align: 'left',
  },
  { key: 'signal', label: 'Dominant signal', title: 'Most common discussion type: direction (Bullish/Bearish) \u00d7 style (Analytical = data-backed; Hype-driven = momentum/FOMO). Click to sort, or use the chips above to filter.' },
  { key: 'flags', label: 'Warnings' },
]

// Sort order for dominant_signal: group analytical-bull first (the most
// actionable bucket), then analytical-bear, hype-bull, hype-bear, sentiment
// fallbacks, then unknown. Numeric so desc/asc both produce intuitive orders.
const _SIGNAL_RANK: Record<string, number> = {
  bull_researched: 0,
  bear_researched: 1,
  bull_emotional:  2,
  bear_emotional:  3,
  bullish:         4,
  bearish:         5,
  unknown:         6,
}

function getSortValue(row: AcsScore, key: SortKey): number | string {
  switch (key) {
    case 'ticker':    return row.ticker
    case 'acs':       return row.acs
    case 'decay_acs': return row.decay_acs
    case 'stage':     return row.lifecycle_stage
    case 'flags':     return row.flags.length
    case 'streak':    return row.stage_streak_days ?? 0
    // Null slope sorts as -Infinity so "Fading" stays at the bottom of desc sort.
    case 'slope':     return row.acs_slope_14d ?? Number.NEGATIVE_INFINITY
    case 'signal':    return _SIGNAL_RANK[row.dominant_signal] ?? 99
  }
}

// Dominant-signal filter chips. Each chip is a predicate over the raw
// dominant_signal string. "All" disables the filter.
type SignalFilter = 'all' | 'bullish' | 'bearish' | 'analytical' | 'hype'

function matchesSignal(signal: string, f: SignalFilter): boolean {
  switch (f) {
    case 'all':        return true
    case 'bullish':    return signal.startsWith('bull')
    case 'bearish':    return signal.startsWith('bear')
    case 'analytical': return signal.endsWith('_researched')
    case 'hype':       return signal.endsWith('_emotional')
  }
}

const FLAG_LABELS: Record<string, string> = {
  gini_high:         'Concentrated posts',
  decelerating_3d:   'Fading momentum',
  late_stage:        'Late stage',
  small_cap:         'Small cap',
  small_cap_haircut: 'Small cap',
  low_unique_authors: 'Few authors',
  cold_start:        'Early signal',
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

export function NarrativeTickerTable({ rows, emptyMessage, loading, onSelect, showContinuity = false }: NarrativeTickerTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('acs')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  // ADR-0023 — orthogonal continuity filter. Default 'all' so Emerging still
  // shows the full stage-1–3 set; chips narrow it client-side without a refetch.
  type ContinuityFilter = 'all' | 'new' | 'sustaining' | 'declining'
  const [continuityFilter, setContinuityFilter] = useState<ContinuityFilter>('all')
  const [signalFilter, setSignalFilter] = useState<SignalFilter>('all')

  const visibleColumns = useMemo(
    () => COLUMNS.filter((c) => showContinuity || !c.continuityOnly),
    [showContinuity],
  )

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (signalFilter !== 'all' && !matchesSignal(r.dominant_signal, signalFilter)) return false
      if (!showContinuity || continuityFilter === 'all') return true
      const streak = r.stage_streak_days ?? 0
      const slope = r.acs_slope_14d
      if (continuityFilter === 'new')        return streak <= 7
      if (continuityFilter === 'sustaining') return streak >= 14 && (slope ?? 0) >= 0
      if (continuityFilter === 'declining')  return slope != null && slope < 0
      return true
    })
  }, [rows, showContinuity, continuityFilter, signalFilter])

  const sorted = useMemo(() => {
    const copy = [...filtered]
    copy.sort((a, b) => {
      const va = getSortValue(a, sortKey)
      const vb = getSortValue(b, sortKey)
      let cmp: number
      if (typeof va === 'number' && typeof vb === 'number') cmp = va - vb
      else cmp = String(va).localeCompare(String(vb))
      return sortDir === 'asc' ? cmp : -cmp
    })
    return copy
  }, [filtered, sortKey, sortDir])

  if (loading && rows.length === 0) {
    return <p className="muted">Loading…</p>
  }

  if (rows.length === 0) {
    return <p className="muted">{emptyMessage}</p>
  }

  if (sorted.length === 0) {
    return <p className="muted">No tickers match the active Narrative filters.</p>
  }

  const onHeaderClick = (key: SortKey | null) => {
    if (key == null) return
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      // 'ticker' and 'signal' read more naturally ascending (A→Z, best→worst);
      // numeric metrics default to descending so the top rows are the strongest.
      setSortDir(key === 'ticker' || key === 'signal' ? 'asc' : 'desc')
    }
  }

  return (
    <div className="table-wrapper">
      {showContinuity && (
        <div className="continuity-filters" role="group" aria-label="Continuity filters">
          {(['all', 'new', 'sustaining', 'declining'] as const).map((f) => (
            <button
              key={f}
              type="button"
              className={`continuity-chip${continuityFilter === f ? ' active' : ''}`}
              onClick={() => setContinuityFilter(f)}
              title={
                f === 'new'        ? 'Streak \u2264 7 days \u2014 newly emerging' :
                f === 'sustaining' ? 'Streak \u2265 14 days and ACS slope \u2265 0 \u2014 durable narratives' :
                f === 'declining'  ? 'ACS slope < 0 \u2014 momentum cooling' :
                                     'Show all rows'
              }
            >
              {f === 'all' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      )}
      <div className="continuity-filters" role="group" aria-label="Dominant signal filters">
        {(['all', 'bullish', 'bearish', 'analytical', 'hype'] as const).map((f) => (
          <button
            key={f}
            type="button"
            className={`continuity-chip${signalFilter === f ? ' active' : ''}`}
            onClick={() => setSignalFilter(f)}
            title={
              f === 'bullish'    ? 'Direction = bull (researched or hype-driven)' :
              f === 'bearish'    ? 'Direction = bear (researched or hype-driven)' :
              f === 'analytical' ? 'Substance = researched (data-backed discussion)' :
              f === 'hype'       ? 'Substance = emotional (momentum / FOMO discussion)' :
                                   'Show all signal types'
            }
          >
            {f === 'all' ? 'All signals' : f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>
      <table className="screener-table narrative-table">
        <colgroup>
          {visibleColumns.map((col) => (
            <col key={col.label} className={`col-${col.key ?? 'breakdown'}`} />
          ))}
        </colgroup>
      <thead>
        <tr>
          {visibleColumns.map((col) => {
            const sortable = col.key != null
            const active = col.key === sortKey
            const colClass = `col-${col.key ?? 'breakdown'}`
            const classes = [colClass, sortable ? 'sortable' : ''].filter(Boolean).join(' ')
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
                  {(() => {
                    const dir = acsDirectionClass(row.dominant_signal)
                    if (!dir) return null
                    const bull = dir === 'acs-direction-bull'
                    return (
                      <span
                        className={`acs-direction ${dir}`}
                        title={`Dominant signal: ${labelSignal(row.dominant_signal)}`}
                      >
                        {bull ? '▲ bull' : '▼ bear'}
                      </span>
                    )
                  })()}
                </div>
              </td>

              <td style={{ textAlign: 'center' }}>
                <StageBadge stage={row.lifecycle_stage} confidence={row.stage_confidence} />
              </td>
              {showContinuity && (
                <>
                  <td style={{ textAlign: 'center' }} title={row.first_emerged_at ? `Since ${row.first_emerged_at}` : undefined}>
                    {(row.stage_streak_days ?? 0) > 0 ? `${row.stage_streak_days}d` : '\u2014'}
                  </td>
                  <td style={{ textAlign: 'center' }} className={row.acs_slope_14d != null ? (row.acs_slope_14d >= 0 ? 'slope-up' : 'slope-down') : 'muted'}>
                    {row.acs_slope_14d == null
                      ? '\u2014'
                      : `${row.acs_slope_14d >= 0 ? '\u2197' : '\u2198'} ${row.acs_slope_14d.toFixed(2)}`}
                  </td>
                </>
              )}
              <td>
                <div className="acs-pills">
                  <ComponentPill letter="A" value={row.components.a_attention_persistence} title="Daily activity score: how consistently it's been discussed over 14 days (max 30)" />
                  <ComponentPill letter="B" value={row.components.b_contributor_quality} title="Post diversity score: many different people posting, not one account dominating (max 25)" />
                  <ComponentPill letter="C" value={row.components.c_narrative_strength} title="Narrative coherence: posts share a common thesis (max 25) — needs hourly detector to run" />
                  <ComponentPill letter="D" value={row.components.d_thesis_quality} title="Analytical depth: fraction of posts that include real research, not just hype (max 20)" />
                </div>
              </td>
              <td className="col-signal" title={labelSignal(row.dominant_signal)}>{labelSignal(row.dominant_signal)}</td>
              <td className="muted col-flags" title={humanizeFlags(row.flags)}>{humanizeFlags(row.flags)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
    </div>
  )
}

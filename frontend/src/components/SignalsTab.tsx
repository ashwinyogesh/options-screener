import { useMemo, useState } from 'react'
import { useSignals } from '../hooks/useSignals'
import type { HorizonStats, SignalEvent, SignalsFilters } from '../types/signals'

const PCT = (v: number | null, digits = 1): string =>
  v == null ? '—' : `${(v * 100).toFixed(digits)}%`

const NUM = (v: number | null, digits = 2): string =>
  v == null ? '—' : v.toFixed(digits)

const TRANSITION_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Any' },
  { value: '1to2', label: '1 → 2' },
  { value: '2to3', label: '2 → 3' },
  { value: '3to4', label: '3 → 4' },
  { value: '4to5', label: '4 → 5' },
  { value: '5to6', label: '5 → 6' },
  { value: '0to1', label: 'Cold-start (0 → 1)' },
]

function excessClass(v: number | null): string {
  if (v == null) return ''
  if (v > 0.005) return 'excess-pos'
  if (v < -0.005) return 'excess-neg'
  return ''
}

type FillStage = 'queued' | 't0' | 't5' | 't10' | 'complete'

interface FillState {
  stage: FillStage
  label: string
  /** Progress 0..1 for tinting the pill. */
  progress: number
}

function fillState(row: SignalEvent): FillState {
  if (row.px_t20 != null) return { stage: 'complete', label: 'complete', progress: 1 }
  if (row.px_t10 != null) return { stage: 't10', label: 'T+10', progress: 0.75 }
  if (row.px_t5 != null) return { stage: 't5', label: 'T+5', progress: 0.5 }
  if (row.px_at_signal != null) return { stage: 't0', label: 'T+0', progress: 0.25 }
  return { stage: 'queued', label: 'queued', progress: 0 }
}

function StatCard({ stats }: { stats: HorizonStats }) {
  const hit = stats.hit_rate
  // Hit rate colour: green ≥0.55, amber 0.45–0.55, red <0.45.
  // Below 5 samples we always show neutral — too noisy to colour.
  let hitClass = ''
  if (hit != null && stats.n_complete >= 5) {
    if (hit >= 0.55) hitClass = 'stat-hit-strong'
    else if (hit >= 0.45) hitClass = 'stat-hit-mid'
    else hitClass = 'stat-hit-weak'
  }
  return (
    <div className="signals-stat-card">
      <div className="signals-stat-label">T+{stats.horizon_days} days</div>
      <div className={`signals-stat-hit ${hitClass}`}>{PCT(hit, 0)}</div>
      <div className="signals-stat-sub">
        Hit rate · n = {stats.n_complete}
      </div>
      <div className="signals-stat-median">
        Median excess: <strong>{PCT(stats.median_excess_return, 2)}</strong>
      </div>
    </div>
  )
}

function FilterBar({
  filters,
  setFilters,
  loading,
  onRefresh,
}: {
  filters: SignalsFilters
  setFilters: (next: SignalsFilters) => void
  loading: boolean
  onRefresh: () => void
}) {
  return (
    <div className="signals-filter-bar">
      <label>
        Transition
        <select
          value={filters.transition ?? ''}
          onChange={(e) =>
            setFilters({ ...filters, transition: e.target.value || null })
          }
        >
          {TRANSITION_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
      <label>
        Min confidence
        <select
          value={filters.minConfidence ?? ''}
          onChange={(e) => {
            const v = e.target.value
            setFilters({ ...filters, minConfidence: v ? parseFloat(v) : null })
          }}
        >
          <option value="">Any</option>
          <option value="0.5">≥ 0.50</option>
          <option value="0.7">≥ 0.70</option>
          <option value="0.85">≥ 0.85</option>
        </select>
      </label>
      <label>
        Since
        <input
          type="date"
          value={filters.since ?? ''}
          onChange={(e) =>
            setFilters({ ...filters, since: e.target.value || null })
          }
        />
      </label>
      <label>
        Ticker
        <input
          type="text"
          maxLength={10}
          placeholder="e.g. NVDA"
          value={filters.ticker ?? ''}
          onChange={(e) => {
            const cleaned = e.target.value.toUpperCase().replace(/[^A-Z0-9.\-]/g, '')
            setFilters({ ...filters, ticker: cleaned || null })
          }}
        />
      </label>
      <button className="btn-secondary" onClick={onRefresh} disabled={loading}>
        {loading ? 'Loading…' : 'Refresh'}
      </button>
    </div>
  )
}

function SignalsRow({ row }: { row: SignalEvent }) {
  const fill = fillState(row)
  return (
    <tr>
      <td className="ticker-cell">{row.ticker}</td>
      <td>{row.event_date}</td>
      <td className="center">{row.prev_stage} → {row.new_stage}</td>
      <td className="right">{NUM(row.confidence, 2)}</td>
      <td className="right">{NUM(row.breadth_score, 2)}</td>
      <td className={`right ${excessClass(row.excess_t5)}`}>{PCT(row.excess_t5, 2)}</td>
      <td className={`right ${excessClass(row.excess_t10)}`}>{PCT(row.excess_t10, 2)}</td>
      <td className={`right ${excessClass(row.excess_t20)}`}>{PCT(row.excess_t20, 2)}</td>
      <td className="center">
        <span
          className={`pill pill-fill pill-fill-${fill.stage}`}
          title={`Backfill progress: ${fill.label}`}
        >
          {fill.label}
        </span>
      </td>
    </tr>
  )
}

export function SignalsTab() {
  const { data, loading, error, filters, setFilters, refresh } = useSignals()
  const [showOnlyHydrated, setShowOnlyHydrated] = useState(false)

  const rows = useMemo(
    () =>
      showOnlyHydrated
        ? data.events.filter((e) => e.px_t20 != null)
        : data.events,
    [data.events, showOnlyHydrated],
  )

  return (
    <section className="signals-tab">
      <header className="signals-header">
        <h3>Signal Performance</h3>
        <p className="signals-subtitle">
          Forward-price performance of every stage transition emitted by the
          narrative detector. Hit rate = share of events whose excess return
          vs SPY is positive at the horizon. Backfill runs daily after the US
          close — recent events show <em>pending</em> until then.
        </p>
      </header>

      <div className="signals-stat-row">
        {data.horizons.map((h) => (
          <StatCard key={h.horizon_days} stats={h} />
        ))}
      </div>

      <FilterBar
        filters={filters}
        setFilters={setFilters}
        loading={loading}
        onRefresh={() => void refresh()}
      />

      <div className="signals-table-controls">
        <label className="signals-toggle">
          <input
            type="checkbox"
            checked={showOnlyHydrated}
            onChange={(e) => setShowOnlyHydrated(e.target.checked)}
          />
          Show only fully-backfilled events
        </label>
        <span className="muted">
          {rows.length} of {data.n_total} events
        </span>
      </div>

      {error && !error.unavailable && (
        <div className="error-banner">{error.detail}</div>
      )}
      {error?.unavailable && (
        <div className="info-banner">
          <strong>Signal log unavailable.</strong> {error.detail}
        </div>
      )}

      <div className="table-wrapper">
        <table className="screener-table signals-table">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Event date</th>
              <th className="center">Stage</th>
              <th className="right" title="Stage confidence at emission">Conf.</th>
              <th className="right" title="Narrative breadth score at emission">Breadth</th>
              <th className="right" title="Ticker return − SPY return at T+5 trading days">T+5</th>
              <th className="right" title="Ticker return − SPY return at T+10">T+10</th>
              <th className="right" title="Ticker return − SPY return at T+20">T+20</th>
              <th className="center">Backfill</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !loading && (
              <tr>
                <td colSpan={9} className="empty-state">
                  No transitions emitted yet for these filters.
                </td>
              </tr>
            )}
            {rows.map((r) => <SignalsRow key={r.id} row={r} />)}
          </tbody>
        </table>
      </div>
    </section>
  )
}

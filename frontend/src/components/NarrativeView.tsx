import { useCallback, useEffect, useState } from 'react'
import { useNarrative } from '../hooks/useNarrative'
import type { NarrativeError, TickerDetail } from '../types/narrative'
import { NarrativeIcMonitor } from './NarrativeIcMonitor'
import { NarrativeReadingGuide } from './NarrativeReadingGuide'
import { NarrativeTickerTable } from './NarrativeTickerTable'
import { ScoreLegend } from './ScoreLegend'
import { SignalsTab } from './SignalsTab'
import { TickerDetailPanel } from './TickerDetailPanel'
import { TickerSearch } from './TickerSearch'

type ActiveTab = 'scores' | 'ic-monitor'

function formatRelative(date: Date): string {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
  if (seconds < 30) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

const STALE_THRESHOLD_MS = 10 * 60 * 1000  // 10 min

export function NarrativeView() {
  const {
    top,
    emerging,
    loading,
    error,
    lastUpdatedAt,
    refresh,
    fetchDetail,
  } = useNarrative()

  const [selectedTicker, setSelectedTicker] = useState<string | null>(null)
  const [detail, setDetail] = useState<TickerDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<ActiveTab>('scores')
  const [, setTick] = useState(0)  // re-render so "X min ago" updates

  // Re-render every 30s so the "updated X min ago" text stays fresh.
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), 30_000)
    return () => window.clearInterval(id)
  }, [])

  const loadDetail = useCallback(
    async (ticker: string) => {
      setSelectedTicker(ticker)
      setDetail(null)
      setDetailError(null)
      setDetailLoading(true)
      const { data, error: err } = await fetchDetail(ticker)
      if (err) setDetailError(formatDetailError(err, ticker))
      else setDetail(data)
      setDetailLoading(false)
    },
    [fetchDetail],
  )

  const closeDetail = () => {
    setSelectedTicker(null)
    setDetail(null)
    setDetailError(null)
  }

  const isStale =
    lastUpdatedAt != null && Date.now() - lastUpdatedAt.getTime() > STALE_THRESHOLD_MS

  return (
    <section className="narrative-view">
      <header className="narrative-header">
        <div>
          <h2>Narrative Intelligence</h2>
          <p className="narrative-subtitle">
            Reddit-driven attention &amp; conviction — surfacing companies in stages 2–3
            of the narrative lifecycle, before institutional consensus.
          </p>
        </div>
        <div className="narrative-header-controls">
          <TickerSearch onSearch={(t) => void loadDetail(t)} disabled={loading} />
          <div className="narrative-refresh-block">
            {lastUpdatedAt && (
              <span
                className={isStale ? 'muted stale' : 'muted'}
                title={lastUpdatedAt.toISOString()}
              >
                Updated {formatRelative(lastUpdatedAt)}
                {isStale ? ' · stale' : ''}
              </span>
            )}
            <button className="btn-secondary" onClick={() => void refresh()} disabled={loading}>
              {loading ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        </div>
      </header>

      {error?.unavailable && (
        <div className="info-banner">
          <strong>Narrative data unavailable.</strong> {error.detail}
          <br />
          <small>
            Phase 6 scorer is live; if you see this banner the API can't reach Cosmos.
            See <code>docs/NARRATIVE_METHODOLOGY.md §8</code> for operational rollout.
          </small>
        </div>
      )}

      {error && !error.unavailable && (
        <div className="error-banner">{error.detail}</div>
      )}

      <ScoreLegend />

      <NarrativeReadingGuide />

      {/* Tab switcher */}
      <div className="tab-bar" style={{ marginLeft: 0, marginBottom: '16px' }}>
        <button
          className={`tab-btn${activeTab === 'scores' ? ' tab-btn-active' : ''}`}
          onClick={() => setActiveTab('scores')}
        >
          Scores
        </button>
        <button
          className={`tab-btn${activeTab === 'ic-monitor' ? ' tab-btn-active' : ''}`}
          onClick={() => setActiveTab('ic-monitor')}
        >
          IC Monitor (90d)
        </button>
      </div>

      {activeTab === 'scores' && (
        <>
          <div className="narrative-grid">
            <section>
              <h3>Top by ACS</h3>
              <dl className="acs-component-legend" aria-label="ACS component legend">
                <div><dt>A</dt><dd>Daily activity — how consistently the ticker is discussed over 14d (max 30)</dd></div>
                <div><dt>B</dt><dd>Post diversity — many distinct authors, not one account dominating (max 25)</dd></div>
                <div><dt>C</dt><dd>Narrative coherence — posts share a common thesis (max 25)</dd></div>
                <div><dt>D</dt><dd>Analytical depth — fraction of posts with real research, not just hype (max 20)</dd></div>
              </dl>
              <NarrativeTickerTable
                rows={top}
                emptyMessage="No ACS scores yet."
                loading={loading}
                onSelect={(t) => void loadDetail(t)}
              />
            </section>
            <section>
              <h3>Emerging (stages 1–3)</h3>
              <NarrativeTickerTable
                rows={emerging}
                emptyMessage="No emerging tickers yet."
                loading={loading}
                onSelect={(t) => void loadDetail(t)}
                showContinuity
              />
            </section>
          </div>
          <SignalsTab />
        </>
      )}

      {activeTab === 'ic-monitor' && (
        <NarrativeIcMonitor />
      )}

      {selectedTicker && (
        <TickerDetailPanel
          detail={detail}
          loading={detailLoading}
          error={detailError}
          onClose={closeDetail}
        />
      )}
    </section>
  )
}

function formatDetailError(err: NarrativeError, ticker: string): string {
  if (err.unavailable) return `Narrative platform unavailable: ${err.detail}`
  if (/404/.test(err.detail)) return `${ticker} is not currently tracked.`
  return err.detail
}

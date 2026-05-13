import { useNarrative } from '../hooks/useNarrative'
import { NarrativeTickerTable } from './NarrativeTickerTable'
import { NarrativeAlertList } from './NarrativeAlertList'

export function NarrativeView() {
  const { top, emerging, alerts, loading, error, refresh } = useNarrative()

  return (
    <section className="narrative-view">
      <header className="narrative-header">
        <div>
          <h2>Narrative Intelligence</h2>
          <p className="narrative-subtitle">
            Reddit-driven attention &amp; conviction — surfacing companies in stages 1–3
            of the narrative lifecycle, before institutional consensus.
          </p>
        </div>
        <button className="btn-secondary" onClick={() => void refresh()} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      {error?.unavailable && (
        <div className="info-banner">
          <strong>Platform not yet provisioned.</strong> {error.detail}
          <br />
          <small>
            See <code>docs/NARRATIVE_METHODOLOGY.md §8</code> for the phased rollout.
            UI integrates against Phase 0 stubs; data appears once Phase 6 ships.
          </small>
        </div>
      )}

      {error && !error.unavailable && (
        <div className="error-banner">{error.detail}</div>
      )}

      <div className="narrative-grid">
        <section>
          <h3>Top by ACS</h3>
          <NarrativeTickerTable rows={top} emptyMessage="No ACS scores yet." />
        </section>
        <section>
          <h3>Emerging (stages 1–3)</h3>
          <NarrativeTickerTable rows={emerging} emptyMessage="No emerging tickers yet." />
        </section>
        <section>
          <h3>Alerts</h3>
          <NarrativeAlertList alerts={alerts} />
        </section>
      </div>
    </section>
  )
}

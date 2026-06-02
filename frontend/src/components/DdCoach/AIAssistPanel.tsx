import { useCallback, useState } from 'react'
import { useDdCoach } from '../../hooks/useDdCoach'
import type {
  BearScaffoldContent,
  BusinessSummaryContent,
  InsightType,
  IntelResult,
  LeadershipContent,
  MdaSummaryContent,
  RiskDiffContent,
} from '../../types/ddCoach'

interface Props {
  ticker: string
  insightType: InsightType
  title: string
  subtitle?: string
  /** Auto-open on mount (default false — user clicks to fetch). */
  defaultOpen?: boolean
}

/**
 * Collapsible "AI assist" panel that lazy-fetches an LLM-derived insight
 * from the backend on first expand. Same component shape per screen; the
 * insight_type discriminates the rendered body.
 *
 * Cache is server-side (Cosmos, keyed by accession#) so re-mounts and
 * future sessions are free after the first call per filing.
 */
export function AIAssistPanel({ ticker, insightType, title, subtitle, defaultOpen = false }: Props) {
  const coach = useDdCoach()
  const [open, setOpen] = useState(defaultOpen)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<IntelResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (force = false) => {
    setLoading(true)
    setError(null)
    const { data, error: err } = await coach.fetchIntel(ticker, insightType, { force })
    setLoading(false)
    if (err) {
      setError(err.detail)
      return
    }
    if (data) setResult(data)
  }, [coach, ticker, insightType])

  const onToggle = useCallback(() => {
    const next = !open
    setOpen(next)
    if (next && !result && !loading) {
      void load(false)
    }
  }, [open, result, loading, load])

  return (
    <section className="dd-ai-panel">
      <button
        type="button"
        className="dd-ai-toggle"
        onClick={onToggle}
        aria-expanded={open}
      >
        <span className="dd-ai-icon" aria-hidden="true">{open ? '▾' : '▸'}</span>
        <span className="dd-ai-title">{title}</span>
        {subtitle && <span className="dd-ai-subtitle">— {subtitle}</span>}
      </button>
      {open && (
        <div className="dd-ai-body">
          {loading && <p className="dd-ai-loading">Reading the filing… this can take 10–20 seconds the first time.</p>}
          {error && (
            <div className="dd-ai-error">
              <p>Couldn't generate this analysis: {error}</p>
              <button type="button" className="btn-secondary" onClick={() => void load(false)}>Retry</button>
            </div>
          )}
          {!loading && !error && result && (
            <IntelBody insightType={insightType} content={result.content as Record<string, unknown>} />
          )}
          {result && (
            <footer className="dd-ai-footer">
              <span className="dd-ai-meta">
                {result.cached ? 'Cached' : 'Freshly generated'} · sources:{' '}
                {result.sources.map((s, i) => (
                  <span key={`${s.form}-${s.accession}-${i}`}>
                    {i > 0 && ', '}
                    {s.primary_doc_url ? (
                      <a href={s.primary_doc_url} target="_blank" rel="noreferrer">{s.form} ({s.filing_date})</a>
                    ) : (
                      <span>{s.form}</span>
                    )}
                  </span>
                ))}
              </span>
              <button type="button" className="dd-ai-refresh" onClick={() => void load(true)}>
                Regenerate
              </button>
            </footer>
          )}
        </div>
      )}
    </section>
  )
}


// ---------------------------------------------------------------------------
// Per-insight body renderers
// ---------------------------------------------------------------------------

function IntelBody({ insightType, content }: { insightType: InsightType; content: Record<string, unknown> }) {
  switch (insightType) {
    case 'business_summary':
      return <BusinessSummaryBody c={content as unknown as BusinessSummaryContent} />
    case 'mda_summary':
      return <MdaSummaryBody c={content as unknown as MdaSummaryContent} />
    case 'risk_diff':
      return <RiskDiffBody c={content as unknown as RiskDiffContent} />
    case 'leadership':
      return <LeadershipBody c={content as unknown as LeadershipContent} />
    case 'bear_scaffold':
      return <BearScaffoldBody c={content as unknown as BearScaffoldContent} />
    default:
      return null
  }
}


function BusinessSummaryBody({ c }: { c: BusinessSummaryContent }) {
  return (
    <div className="dd-ai-content">
      <p className="dd-ai-lead">{c.summary}</p>
      {c.primary_products?.length > 0 && (
        <div className="dd-ai-section">
          <h5>What they sell</h5>
          <ul>{c.primary_products.map(p => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
      <div className="dd-ai-section"><h5>Who pays them</h5><p>{c.main_customers}</p></div>
      <div className="dd-ai-section"><h5>Moat hypothesis</h5><p>{c.moat_hypothesis}</p></div>
      {c.segments?.length > 0 && (
        <div className="dd-ai-section">
          <h5>Segments</h5>
          <ul>{c.segments.map(s => <li key={s}>{s}</li>)}</ul>
        </div>
      )}
    </div>
  )
}


function MdaSummaryBody({ c }: { c: MdaSummaryContent }) {
  return (
    <div className="dd-ai-content">
      <div className="dd-ai-section"><h5>Revenue bridge</h5><p>{c.revenue_bridge}</p></div>
      <div className="dd-ai-section"><h5>Margin drivers</h5><p>{c.margin_drivers}</p></div>
      <div className="dd-ai-section"><h5>Liquidity</h5><p>{c.liquidity}</p></div>
      <div className="dd-ai-section">
        <h5>Forward tone</h5>
        <p><span className={`dd-ai-tone dd-ai-tone-${c.forward_tone}`}>{c.forward_tone}</span></p>
      </div>
      {c.highlights?.length > 0 && (
        <div className="dd-ai-section">
          <h5>Highlights</h5>
          <ul>{c.highlights.map(h => <li key={h}>{h}</li>)}</ul>
        </div>
      )}
    </div>
  )
}


function RiskDiffBody({ c }: { c: RiskDiffContent }) {
  return (
    <div className="dd-ai-content">
      <p>Overall tone vs prior year: <strong>{c.overall_tone}</strong></p>
      {c.new_risks?.length > 0 ? (
        <div className="dd-ai-section">
          <h5>New risks this year</h5>
          <ul className="dd-ai-risks">
            {c.new_risks.map(r => (
              <li key={r.title}>
                <span className={`dd-ai-sev dd-ai-sev-${r.severity}`}>{r.severity}</span>
                <strong>{r.title}</strong>: {r.summary}
              </li>
            ))}
          </ul>
        </div>
      ) : <p>No genuinely new risks identified.</p>}
      {c.expanded_risks?.length > 0 && (
        <div className="dd-ai-section">
          <h5>Materially expanded</h5>
          <ul className="dd-ai-risks">
            {c.expanded_risks.map(r => (
              <li key={r.title}>
                <span className={`dd-ai-sev dd-ai-sev-${r.severity}`}>{r.severity}</span>
                <strong>{r.title}</strong>: {r.what_changed}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}


function LeadershipBody({ c }: { c: LeadershipContent }) {
  return (
    <div className="dd-ai-content">
      <div className="dd-ai-section">
        <h5>CEO</h5>
        <p><strong>{c.ceo_name}</strong> — {c.ceo_tenure_note}</p>
      </div>
      <div className="dd-ai-section">
        <h5>Compensation alignment</h5>
        <p><span className={`dd-ai-align dd-ai-align-${c.comp_alignment.replace(/[^a-z]/g, '-')}`}>{c.comp_alignment}</span></p>
        <p>{c.comp_summary}</p>
      </div>
      <div className="dd-ai-section">
        <h5>Insider activity (qualitative)</h5>
        <p>{c.insider_activity_note}</p>
      </div>
      {c.concerns?.length > 0 && (
        <div className="dd-ai-section">
          <h5>Concerns to flag</h5>
          <ul>{c.concerns.map(x => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
    </div>
  )
}


function BearScaffoldBody({ c }: { c: BearScaffoldContent }) {
  return (
    <div className="dd-ai-content">
      <p className="dd-ai-disclaimer">
        These are stress-tests, not predictions — they help you check whether your thesis survives plausible failure modes.
      </p>
      <ol className="dd-ai-scenarios">
        {c.scenarios.map(s => (
          <li key={s.title} className="dd-ai-scenario">
            <h5>{s.title} <small>({s.probability_range_pct})</small></h5>
            <p>{s.narrative}</p>
            <p className="dd-ai-watch"><em>Watch:</em> {s.metric_to_watch}</p>
          </li>
        ))}
      </ol>
    </div>
  )
}

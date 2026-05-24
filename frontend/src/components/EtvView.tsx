import { useState } from 'react'
import { useEtv } from '../hooks/useEtv'
import type {
  EtvData,
  EtvHorizon,
  EtvPipelineLogEntry,
  EtvRiskTolerance,
  EtvScenario,
  EtvReport,
} from '../types/etv'

// ----------------------------------------------------------------- utils ---
const fmtCur = (v: number | null | undefined) =>
  v == null ? '—' : `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
const fmtBigCur = (v: number | null | undefined) => {
  if (v == null) return '—'
  const a = Math.abs(v)
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  if (a >= 1e3) return `$${(v / 1e3).toFixed(1)}K`
  return fmtCur(v)
}
const fmtPct = (v: number | null | undefined, d = 1) =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`
const fmtPctRaw = (v: number | null | undefined, d = 1) =>
  v == null ? '—' : `${v.toFixed(d)}%`
const fmtFrac = (v: number | null | undefined, d = 1) =>
  v == null ? '—' : `${(v * 100).toFixed(d)}%`
const fmtNum = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: d })

const SCENARIO_COLORS = { bear: '#f87171', base: '#60a5fa', bull: '#4ade80' } as const
const DECISION_COLORS: Record<string, string> = {
  TRADE: '#4ade80',
  'NO TRADE': '#f87171',
}
const SENTIMENT_COLORS: Record<string, string> = {
  Euphoric: '#22c55e',
  Positive: '#4ade80',
  Neutral: '#94a3b8',
  Negative: '#fbbf24',
  Fearful: '#f87171',
}
const CONF_COLORS: Record<string, string> = {
  High: '#4ade80',
  Medium: '#fbbf24',
  Low: '#f87171',
}

// ============================================================ primitives ===
function Stat({
  label,
  value,
  color,
}: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          color: '#94a3b8',
          textTransform: 'uppercase',
          letterSpacing: 0.5,
        }}
      >
        {label}
      </div>
      <div style={{ fontSize: 14, color: color ?? '#e2e8f0', fontWeight: 600 }}>
        {value}
      </div>
    </div>
  )
}

function Card({
  title,
  step,
  children,
}: { title: string; step?: string; children: React.ReactNode }) {
  return (
    <section
      style={{
        background: '#1e293b',
        border: '1px solid #334155',
        borderRadius: 8,
        padding: '14px 18px',
        marginBottom: 14,
      }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          color: '#cbd5e1',
          textTransform: 'uppercase',
          letterSpacing: 0.8,
          marginBottom: 10,
          display: 'flex',
          gap: 10,
          alignItems: 'baseline',
        }}
      >
        {step && <span style={{ color: '#64748b' }}>{step}</span>}
        <span>{title}</span>
      </div>
      {children}
    </section>
  )
}

function Pills({ items, color = '#334155' }: { items: string[]; color?: string }) {
  if (!items?.length) return <span style={{ color: '#64748b', fontSize: 12 }}>—</span>
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {items.map((x, i) => (
        <span
          key={i}
          style={{
            fontSize: 11,
            padding: '2px 8px',
            borderRadius: 12,
            background: color,
            color: '#e2e8f0',
          }}
        >
          {x}
        </span>
      ))}
    </div>
  )
}

function Bullets({ items }: { items: string[] }) {
  if (!items?.length) return <span style={{ color: '#64748b', fontSize: 12 }}>—</span>
  return (
    <ul style={{ margin: 0, paddingLeft: 18, color: '#cbd5e1', fontSize: 13 }}>
      {items.map((x, i) => (
        <li key={i} style={{ marginBottom: 4 }}>
          {x}
        </li>
      ))}
    </ul>
  )
}

function Tag({
  text,
  color,
}: { text: string; color: string }) {
  return (
    <span
      style={{
        fontSize: 12,
        padding: '3px 10px',
        borderRadius: 6,
        background: `${color}22`,
        color,
        border: `1px solid ${color}66`,
        fontWeight: 600,
      }}
    >
      {text}
    </span>
  )
}

// ============================================================== INPUT ===
function EtvInputPanel({
  onRun,
  loading,
}: {
  onRun: (ticker: string, h: EtvHorizon, r: EtvRiskTolerance, refresh: boolean) => void
  loading: boolean
}) {
  const [ticker, setTicker] = useState('')
  const [horizon, setHorizon] = useState<EtvHorizon>('medium')
  const [risk, setRisk] = useState<EtvRiskTolerance>('moderate')

  function submit(refresh: boolean) {
    const t = ticker.trim().toUpperCase()
    if (!t) return
    onRun(t, horizon, risk, refresh)
  }

  return (
    <div
      style={{
        background: '#1e293b',
        border: '1px solid #334155',
        borderRadius: 8,
        padding: 16,
        marginBottom: 16,
        display: 'flex',
        gap: 12,
        flexWrap: 'wrap',
        alignItems: 'flex-end',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <label style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase' }}>
          Ticker
        </label>
        <input
          value={ticker}
          onChange={e => setTicker(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit(false)}
          placeholder="AAPL"
          style={{
            background: '#0f172a',
            border: '1px solid #334155',
            color: '#e2e8f0',
            padding: '6px 10px',
            borderRadius: 6,
            width: 120,
            fontSize: 14,
          }}
        />
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <label style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase' }}>
          Horizon
        </label>
        <select
          value={horizon}
          onChange={e => setHorizon(e.target.value as EtvHorizon)}
          style={{
            background: '#0f172a',
            border: '1px solid #334155',
            color: '#e2e8f0',
            padding: '6px 10px',
            borderRadius: 6,
          }}
        >
          <option value="short">Short (days–weeks)</option>
          <option value="medium">Medium (1–6 months)</option>
          <option value="long">Long (6+ months)</option>
        </select>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <label style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase' }}>
          Risk
        </label>
        <select
          value={risk}
          onChange={e => setRisk(e.target.value as EtvRiskTolerance)}
          style={{
            background: '#0f172a',
            border: '1px solid #334155',
            color: '#e2e8f0',
            padding: '6px 10px',
            borderRadius: 6,
          }}
        >
          <option value="conservative">Conservative</option>
          <option value="moderate">Moderate</option>
          <option value="aggressive">Aggressive</option>
        </select>
      </div>
      <button
        onClick={() => submit(false)}
        disabled={loading || !ticker.trim()}
        style={{
          background: '#2563eb',
          color: 'white',
          border: 'none',
          padding: '8px 16px',
          borderRadius: 6,
          fontWeight: 600,
          cursor: loading ? 'not-allowed' : 'pointer',
          opacity: loading ? 0.6 : 1,
        }}
      >
        {loading ? 'Analysing…' : 'Run ETV'}
      </button>
      <button
        onClick={() => submit(true)}
        disabled={loading || !ticker.trim()}
        style={{
          background: 'transparent',
          color: '#94a3b8',
          border: '1px solid #334155',
          padding: '8px 14px',
          borderRadius: 6,
          fontSize: 12,
          cursor: loading ? 'not-allowed' : 'pointer',
        }}
      >
        Force refresh
      </button>
    </div>
  )
}

// ============================================================== BANNER ==
function DecisionBanner({ d }: { d: EtvData }) {
  const r = d.report
  const col = DECISION_COLORS[r.decision.decision] ?? '#94a3b8'
  const conf = r.decision.confidence_pct ?? 0
  return (
    <div
      style={{
        background: `linear-gradient(90deg, ${col}22, #1e293b 65%)`,
        border: `1px solid ${col}66`,
        borderLeft: `5px solid ${col}`,
        borderRadius: 8,
        padding: '16px 20px',
        marginBottom: 14,
        display: 'grid',
        gridTemplateColumns: 'minmax(200px, auto) 1fr',
        gap: 18,
        alignItems: 'center',
      }}
    >
      <div>
        <div
          style={{
            fontSize: 11,
            color: '#94a3b8',
            textTransform: 'uppercase',
            letterSpacing: 0.6,
          }}
        >
          Decision
        </div>
        <div style={{ fontSize: 28, fontWeight: 800, color: col, letterSpacing: 0.5 }}>
          {r.decision.decision}
        </div>
        <div style={{ fontSize: 13, color: '#cbd5e1', marginTop: 4 }}>
          {r.decision.direction} · {r.decision.horizon} horizon
        </div>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: 12,
        }}
      >
        <Stat
          label="Confidence"
          value={fmtPctRaw(conf, 0)}
          color={conf >= 70 ? '#4ade80' : conf >= 55 ? '#fbbf24' : '#f87171'}
        />
        <Stat label="Current" value={fmtCur(d.grounding.current_price)} />
        <Stat
          label="ETV (weighted)"
          value={fmtCur(r.etv.probability_weighted_etv)}
        />
        <Stat
          label="Expected return"
          value={fmtPct(r.etv.expected_return_pct)}
          color={
            r.etv.expected_return_pct == null
              ? undefined
              : r.etv.expected_return_pct > 0
              ? '#4ade80'
              : '#f87171'
          }
        />
        <Stat
          label="Asymmetry"
          value={r.asymmetry.ratio == null ? '—' : `${r.asymmetry.ratio.toFixed(2)}:1`}
          color={
            r.asymmetry.ratio != null && r.asymmetry.ratio >= 2 ? '#4ade80' : '#fbbf24'
          }
        />
        <Stat
          label="Sizing"
          value={
            r.sizing.recommended_allocation_pct == null
              ? '—'
              : `${r.sizing.recommended_allocation_pct.toFixed(1)}%`
          }
        />
      </div>
    </div>
  )
}

// ============================================================== HEADER ==
function HeaderCard({ d }: { d: EtvData }) {
  const g = d.grounding
  return (
    <div
      style={{
        background: '#1e293b',
        border: '1px solid #334155',
        borderRadius: 8,
        padding: '14px 18px',
        marginBottom: 14,
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
        gap: 12,
      }}
    >
      <div style={{ gridColumn: '1 / -1' }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: '#e2e8f0' }}>
          {g.company_name}{' '}
          <span style={{ color: '#94a3b8', fontWeight: 400 }}>· {g.ticker}</span>
        </div>
        <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 2 }}>
          {g.sector ?? '—'} · {g.industry ?? '—'} · as of {g.as_of}
          {d.cached && (
            <span style={{ color: '#4ade80' }}> · cached ({d.cache_age_sec}s)</span>
          )}
          <span style={{ color: '#64748b' }}> · {d.model}</span>
        </div>
      </div>
      <Stat label="Price" value={fmtCur(g.current_price)} />
      <Stat label="52w range" value={`${fmtCur(g.week52_low)} – ${fmtCur(g.week52_high)}`} />
      <Stat label="Market Cap" value={fmtBigCur(g.market_cap)} />
      <Stat label="EV" value={fmtBigCur(g.enterprise_value)} />
      <Stat label="Rev TTM" value={fmtBigCur(g.revenue_ttm)} />
      <Stat
        label="Rev YoY"
        value={g.revenue_growth_yoy == null ? '—' : fmtFrac(g.revenue_growth_yoy)}
      />
      <Stat
        label="Op Margin"
        value={g.operating_margin == null ? '—' : fmtFrac(g.operating_margin)}
      />
      <Stat label="EBITDA" value={fmtBigCur(g.ebitda)} />
      <Stat label="FCF" value={fmtBigCur(g.free_cash_flow)} />
      <Stat label="Net Debt" value={fmtBigCur(g.net_debt)} />
      <Stat
        label="Fwd P/E"
        value={g.forward_pe == null ? '—' : `${g.forward_pe.toFixed(1)}x`}
      />
      <Stat
        label="EV/EBITDA"
        value={g.ev_ebitda == null ? '—' : `${g.ev_ebitda.toFixed(1)}x`}
      />
      <Stat
        label="IV 30d"
        value={g.implied_vol_30d == null ? '—' : fmtFrac(g.implied_vol_30d, 0)}
      />
      <Stat
        label="Short % float"
        value={g.short_pct_float == null ? '—' : fmtFrac(g.short_pct_float, 1)}
      />
      <Stat
        label="RSI 14"
        value={g.rsi_14 == null ? '—' : g.rsi_14.toFixed(0)}
        color={
          g.rsi_14 == null
            ? undefined
            : g.rsi_14 > 70
            ? '#f87171'
            : g.rsi_14 < 30
            ? '#4ade80'
            : undefined
        }
      />
      <Stat
        label="Analyst tgt"
        value={fmtCur(g.analyst_target_mean)}
      />
    </div>
  )
}

// ============================================== Scenario row component ===
function ScenarioRow({
  label,
  sc,
  tone,
  intrinsicOnly = false,
}: {
  label: string
  sc: EtvScenario
  tone: 'bear' | 'base' | 'bull'
  intrinsicOnly?: boolean
}) {
  const col = SCENARIO_COLORS[tone]
  return (
    <div
      style={{
        borderLeft: `3px solid ${col}`,
        background: '#0f172a',
        borderRadius: 6,
        padding: '10px 14px',
        marginBottom: 8,
      }}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto repeat(auto-fit, minmax(110px, 1fr))',
          gap: 14,
          alignItems: 'center',
        }}
      >
        <div style={{ fontWeight: 700, color: col, minWidth: 80 }}>
          {label}{' '}
          <span style={{ color: '#94a3b8', fontWeight: 500, fontSize: 12 }}>
            {sc.probability_pct == null ? '' : `${sc.probability_pct.toFixed(0)}%`}
          </span>
        </div>
        <Stat label={intrinsicOnly ? 'Intrinsic price' : 'Price / ETV'} value={fmtCur(sc.price)} />
        {!intrinsicOnly && (
          <>
            <Stat label="Regime mult" value={sc.regime_multiplier ?? '—'} />
            <Stat label="Behavior" value={sc.behavior_impact ?? '—'} />
          </>
        )}
      </div>
      <div style={{ marginTop: 8, fontSize: 12, color: '#cbd5e1' }}>
        {sc.rationale}
      </div>
      {sc.value_decomposition && (
        <div style={{ marginTop: 8 }}>
          <div
            style={{
              fontSize: 10,
              color: '#94a3b8',
              textTransform: 'uppercase',
              letterSpacing: 0.6,
              marginBottom: 4,
            }}
          >
            {intrinsicOnly ? 'Intrinsic value (fundamental only)' : 'Value decomposition (Σ = price)'}
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: intrinsicOnly ? '1fr' : 'repeat(5, 1fr)',
              gap: 6,
              fontSize: 11,
            }}
          >
            <Stat label="Fundamental" value={fmtCur(sc.value_decomposition.fundamental)} />
            {!intrinsicOnly && (
              <>
                <Stat label="Regime adj" value={fmtCur(sc.value_decomposition.regime_adjustment)} />
                <Stat label="Mkt-exp adj" value={fmtCur(sc.value_decomposition.market_expectations_adjustment)} />
                <Stat label="Optionality" value={fmtCur(sc.value_decomposition.optionality)} />
                <Stat label="Behavioral" value={fmtCur(sc.value_decomposition.behavioral_premium)} />
              </>
            )}
          </div>
        </div>
      )}
      {sc.conditions?.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div
            style={{
              fontSize: 10,
              color: '#94a3b8',
              textTransform: 'uppercase',
              letterSpacing: 0.6,
              marginBottom: 4,
            }}
          >
            Required conditions
          </div>
          <Pills items={sc.conditions} color="#1e293b" />
        </div>
      )}
    </div>
  )
}

// =================================================== Section renderers ===
function ModelSelectionSection({ r }: { r: EtvReport }) {
  const m = r.model_selection
  return (
    <Card step="§2" title="Model selection & justification">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          gap: 12,
        }}
      >
        <Stat label="Primary archetype" value={m.primary_archetype} />
        <Stat label="Primary model" value={m.primary_model} />
        <Stat
          label="Selection confidence"
          value={m.selection_confidence}
          color={CONF_COLORS[m.selection_confidence]}
        />
      </div>
      <div style={{ marginTop: 10, fontSize: 13, color: '#cbd5e1' }}>
        {m.primary_model_rationale}
      </div>
      <div style={{ marginTop: 10, display: 'grid', gap: 8 }}>
        <div>
          <Label>Secondary archetypes</Label>
          <Pills items={m.secondary_archetypes} />
        </div>
        <div>
          <Label>Supporting models</Label>
          <Pills items={m.supporting_models} color="#1e3a5f" />
        </div>
        <div>
          <Label>Excluded models</Label>
          <Pills items={m.excluded_models} color="#7f1d1d" />
          <div style={{ marginTop: 4, fontSize: 12, color: '#94a3b8' }}>
            {m.excluded_reason}
          </div>
        </div>
      </div>
    </Card>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 10,
        color: '#94a3b8',
        textTransform: 'uppercase',
        letterSpacing: 0.6,
        marginBottom: 4,
      }}
    >
      {children}
    </div>
  )
}

function EconomicValueSection({ r }: { r: EtvReport }) {
  const e = r.economic_value
  return (
    <Card step="§3" title="Model-conditional economic value (intrinsic)">
      <ScenarioRow label="Bear" sc={e.bear} tone="bear" intrinsicOnly />
      <ScenarioRow label="Base" sc={e.base} tone="base" intrinsicOnly />
      <ScenarioRow label="Bull" sc={e.bull} tone="bull" intrinsicOnly />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
          marginTop: 10,
        }}
      >
        <Stat label="Central estimate" value={fmtCur(e.central_estimate)} />
        <Stat
          label="Range"
          value={`${fmtCur(e.low_range)} – ${fmtCur(e.high_range)}`}
        />
      </div>
      <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
        <div>
          <Label>Key drivers</Label>
          <Bullets items={e.key_drivers} />
        </div>
        <div>
          <Label>Key sensitivities</Label>
          <Bullets items={e.key_sensitivities} />
        </div>
      </div>
    </Card>
  )
}

function OptionalitySection({ r }: { r: EtvReport }) {
  const o = r.optionality
  const score = o.structural_score_out_of_10
  return (
    <Card step="§4" title="Strategic / optionality value">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
          gap: 12,
        }}
      >
        <Stat
          label="Structural score"
          value={score == null ? '—' : `${score.toFixed(1)} / 10`}
          color={
            score == null
              ? undefined
              : score >= 7
              ? '#4ade80'
              : score >= 4
              ? '#fbbf24'
              : '#f87171'
          }
        />
        <Stat
          label="Strategic scarcity"
          value={o.strategic_scarcity}
          color={o.strategic_scarcity === 'High' ? '#4ade80' : undefined}
        />
        <Stat
          label="Prob-weighted optionality"
          value={fmtCur(o.probability_weighted)}
        />
        <Stat
          label="Range / share"
          value={`${fmtCur(o.low_realisation)} – ${fmtCur(o.high_realisation)}`}
        />
      </div>
      <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
        <div>
          <Label>Dominant advantages</Label>
          <Pills items={o.dominant_advantages} color="#1e3a5f" />
        </div>
        <div>
          <Label>Pathways</Label>
          <Bullets items={o.pathways} />
        </div>
        <div>
          <Label>Decay risks</Label>
          <Bullets items={o.decay_risks} />
        </div>
      </div>
    </Card>
  )
}

function MarketImpliedSection({ r }: { r: EtvReport }) {
  const m = r.market_implied
  const assessColor =
    m.overall_assessment === 'Underappreciated'
      ? '#4ade80'
      : m.overall_assessment === 'Priced to perfection'
      ? '#f87171'
      : '#fbbf24'
  return (
    <Card step="§5" title="Market-implied expectations">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
          gap: 12,
        }}
      >
        <Stat label="Implied rev growth" value={fmtPctRaw(m.implied_revenue_growth_pct)} />
        <Stat label="Implied margin" value={fmtPctRaw(m.implied_margin_pct)} />
        <Stat
          label="Implied duration"
          value={
            m.implied_growth_duration_years == null
              ? '—'
              : `${m.implied_growth_duration_years.toFixed(1)} yrs`
          }
        />
        <Stat label="Implied TAM capture" value={fmtPctRaw(m.implied_tam_capture_pct)} />
        <Stat
          label="Overall assessment"
          value={m.overall_assessment}
          color={assessColor}
        />
      </div>
      <div style={{ marginTop: 12 }}>
        <Label>Expectation gaps</Label>
        <Bullets items={m.expectation_gaps} />
      </div>
    </Card>
  )
}

function BehaviorSection({ r }: { r: EtvReport }) {
  const b = r.market_behavior
  return (
    <Card step="§6" title="Market behavior & positioning">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
        }}
      >
        <Stat
          label="Sentiment"
          value={b.sentiment}
          color={SENTIMENT_COLORS[b.sentiment]}
        />
        <Stat label="Narrative intensity" value={b.narrative_intensity} />
        <Stat
          label="Crowding risk"
          value={b.crowding_risk}
          color={b.crowding_risk === 'High' ? '#f87171' : undefined}
        />
        <Stat label="Momentum" value={b.momentum} />
        <Stat
          label="Behavioral edge"
          value={b.behavioral_edge}
          color={b.behavioral_edge === 'Yes' ? '#4ade80' : undefined}
        />
      </div>
      <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
        <div>
          <Label>Institutional flow</Label>
          <div style={{ fontSize: 13, color: '#cbd5e1' }}>{b.institutional_flow}</div>
        </div>
        <div>
          <Label>Options positioning</Label>
          <div style={{ fontSize: 13, color: '#cbd5e1' }}>{b.options_positioning}</div>
        </div>
        <div>
          <Label>Key behavioral risks</Label>
          <Bullets items={b.key_risks} />
        </div>
      </div>
    </Card>
  )
}

function RegimeSection({ r }: { r: EtvReport }) {
  const x = r.regime
  return (
    <Card step="§7" title="Market regime">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
          gap: 12,
        }}
      >
        <Stat label="Primary regime" value={x.primary_regime} />
        <Stat
          label="Confidence"
          value={x.confidence}
          color={CONF_COLORS[x.confidence]}
        />
        <Stat label="Model validity" value={x.model_validity} />
        <Stat label="Multiple bias" value={x.multiple_bias} />
        <Stat
          label="Momentum durability"
          value={x.momentum_durability}
          color={CONF_COLORS[x.momentum_durability]}
        />
        <Stat
          label="Transition risk"
          value={fmtPctRaw(x.transition_probability_pct, 0)}
        />
      </div>
      <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
        <div>
          <Label>Secondary regimes</Label>
          <Pills items={x.secondary_regimes} />
        </div>
        <div>
          <Label>Macro drivers</Label>
          <Bullets items={x.macro_drivers} />
        </div>
        <div>
          <Label>Transition trigger</Label>
          <div style={{ fontSize: 13, color: '#cbd5e1' }}>{x.transition_trigger}</div>
        </div>
      </div>
    </Card>
  )
}

function EtvSection({ r }: { r: EtvReport }) {
  const e = r.etv
  return (
    <Card step="§8" title="Expected Tradable Value (ETV)">
      <ScenarioRow label="Bear" sc={e.bear} tone="bear" />
      <ScenarioRow label="Base" sc={e.base} tone="base" />
      <ScenarioRow label="Bull" sc={e.bull} tone="bull" />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
          marginTop: 10,
        }}
      >
        <Stat label="Weighted ETV" value={fmtCur(e.probability_weighted_etv)} />
        <Stat label="Current price" value={fmtCur(e.current_price)} />
        <Stat
          label="Expected return"
          value={fmtPct(e.expected_return_pct)}
          color={
            e.expected_return_pct == null
              ? undefined
              : e.expected_return_pct > 0
              ? '#4ade80'
              : '#f87171'
          }
        />
        <Stat label="Skew" value={e.distribution_skew} />
      </div>
      <div style={{ marginTop: 10, fontSize: 13, color: '#cbd5e1' }}>
        <Label>Primary ETV driver</Label>
        {e.primary_driver}
      </div>
      {e.weighted_decomposition && (
        <div style={{ marginTop: 12, padding: 10, background: '#0f172a', borderRadius: 6 }}>
          <Label>Probability-weighted decomposition (additive, Σ ≈ ETV)</Label>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
              gap: 10,
              marginTop: 6,
            }}
          >
            <Stat label="Fundamental" value={fmtCur(e.weighted_decomposition.fundamental)} />
            <Stat label="Regime adj" value={fmtCur(e.weighted_decomposition.regime_adjustment)} />
            <Stat label="Mkt-exp adj" value={fmtCur(e.weighted_decomposition.market_expectations_adjustment)} />
            <Stat label="Optionality" value={fmtCur(e.weighted_decomposition.optionality)} />
            <Stat label="Behavioral" value={fmtCur(e.weighted_decomposition.behavioral_premium)} />
            <Stat label="Σ check" value={fmtCur(e.weighted_decomposition_sum ?? null)} color="#60a5fa" />
          </div>
        </div>
      )}
    </Card>
  )
}

function RiskSection({ r }: { r: EtvReport }) {
  const x = r.risk
  return (
    <Card step="§9" title="Risk analysis">
      <div style={{ overflowX: 'auto', marginBottom: 12 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: '#94a3b8', textAlign: 'left' }}>
              <th style={{ padding: '6px 8px' }}>Risk</th>
              <th style={{ padding: '6px 8px' }}>Prob</th>
              <th style={{ padding: '6px 8px' }}>Magnitude</th>
              <th style={{ padding: '6px 8px' }}>Expected cost</th>
              <th style={{ padding: '6px 8px' }}>Trigger</th>
            </tr>
          </thead>
          <tbody>
            {x.top_risks.map((row, i) => (
              <tr
                key={i}
                style={{
                  borderTop: '1px solid #334155',
                  color: '#cbd5e1',
                }}
              >
                <td style={{ padding: '6px 8px', fontWeight: 600 }}>{row.name}</td>
                <td style={{ padding: '6px 8px' }}>{fmtPctRaw(row.probability_pct, 0)}</td>
                <td style={{ padding: '6px 8px' }}>{fmtPctRaw(row.magnitude_pct)}</td>
                <td style={{ padding: '6px 8px', color: '#f87171' }}>
                  {fmtPctRaw(row.expected_cost_pct)}
                </td>
                <td style={{ padding: '6px 8px', color: '#94a3b8' }}>{row.trigger}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
        }}
      >
        <Stat label="Stress scenario" value={x.stress_scenario_name} />
        <Stat label="Stress ETV" value={fmtCur(x.stress_etv)} />
        <Stat
          label="Stress return"
          value={fmtPct(x.stress_return_pct)}
          color="#f87171"
        />
        <Stat
          label="Stress prob"
          value={fmtPctRaw(x.stress_probability_pct, 0)}
        />
        <Stat
          label="MAE range"
          value={`${fmtPctRaw(x.mae_low_pct)} – ${fmtPctRaw(x.mae_high_pct)}`}
          color="#f87171"
        />
        <Stat
          label="Risk-adj return"
          value={fmtPct(x.risk_adjusted_expected_return_pct)}
          color={
            x.risk_adjusted_expected_return_pct != null &&
            x.risk_adjusted_expected_return_pct > 0
              ? '#4ade80'
              : '#f87171'
          }
        />
        <Stat
          label="Asymmetry"
          value={x.asymmetry_ratio == null ? '—' : `${x.asymmetry_ratio.toFixed(2)}:1`}
        />
      </div>
    </Card>
  )
}

function AsymmetrySection({ r }: { r: EtvReport }) {
  const a = r.asymmetry
  return (
    <Card step="§10" title="Asymmetry analysis">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: 12,
        }}
      >
        <Stat
          label="Upside (weighted)"
          value={fmtPct(a.upside_pct_weighted)}
          color="#4ade80"
        />
        <Stat
          label="Downside (weighted)"
          value={fmtPct(a.downside_pct_weighted)}
          color="#f87171"
        />
        <Stat
          label="Ratio"
          value={a.ratio == null ? '—' : `${a.ratio.toFixed(2)}:1`}
          color={a.ratio != null && a.ratio >= 2 ? '#4ade80' : '#fbbf24'}
        />
        <Stat
          label="Valid"
          value={a.valid}
          color={a.valid === 'Yes' ? '#4ade80' : a.valid === 'No' ? '#f87171' : '#fbbf24'}
        />
      </div>
      <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
        <div>
          <Label>Edge sources</Label>
          <Pills items={a.edge_sources} color="#1e3a5f" />
        </div>
        <div>
          <Label>Key driver</Label>
          <div style={{ fontSize: 13, color: '#cbd5e1' }}>{a.driver}</div>
        </div>
      </div>
    </Card>
  )
}

function DecisionSection({ r }: { r: EtvReport }) {
  const d = r.decision
  return (
    <Card step="§11" title="Trade decision detail">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
        }}
      >
        <Tag
          text={d.decision}
          color={DECISION_COLORS[d.decision] ?? '#94a3b8'}
        />
        <Stat label="Direction" value={d.direction} />
        <Stat
          label="Confidence"
          value={fmtPctRaw(d.confidence_pct, 0)}
          color={
            d.confidence_pct != null && d.confidence_pct >= 70
              ? '#4ade80'
              : d.confidence_pct != null && d.confidence_pct >= 55
              ? '#fbbf24'
              : '#f87171'
          }
        />
        <Stat label="Horizon" value={d.horizon} />
      </div>
      <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
        <div>
          <Label>Horizon rationale</Label>
          <div style={{ fontSize: 13, color: '#cbd5e1' }}>{d.horizon_rationale}</div>
        </div>
        <div>
          <Label>Catalysts within horizon</Label>
          <Bullets items={d.horizon_catalysts} />
        </div>
        <div>
          <Label>Confidence deductions</Label>
          <Bullets items={d.confidence_deductions} />
        </div>
      </div>
    </Card>
  )
}

function SizingSection({ r }: { r: EtvReport }) {
  const s = r.sizing
  return (
    <Card step="§12" title="Position sizing">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: 12,
        }}
      >
        <Stat label="Raw Kelly" value={fmtPctRaw(s.raw_kelly_pct)} />
        <Stat label="Adjusted Kelly" value={fmtPctRaw(s.adjusted_kelly_pct)} />
        <Stat
          label="Recommended alloc"
          value={fmtPctRaw(s.recommended_allocation_pct)}
          color="#4ade80"
        />
        <Stat label="Max alloc" value={fmtPctRaw(s.max_allocation_pct)} />
        <Stat
          label="Stop-loss"
          value={
            s.stop_loss_price == null
              ? '—'
              : `${fmtCur(s.stop_loss_price)} (${fmtPct(s.stop_loss_pct)})`
          }
          color="#f87171"
        />
        <Stat label="Options structure" value={s.options_structure} />
      </div>
      <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
        <div>
          <Label>Reassessment trigger</Label>
          <div style={{ fontSize: 13, color: '#cbd5e1' }}>{s.reassessment_trigger}</div>
        </div>
        <div>
          <Label>Options rationale</Label>
          <div style={{ fontSize: 13, color: '#cbd5e1' }}>{s.options_rationale}</div>
        </div>
      </div>
    </Card>
  )
}

function CatalystsSection({ r }: { r: EtvReport }) {
  return (
    <Card step="§13" title="Key catalysts">
      <ul style={{ margin: 0, paddingLeft: 18, color: '#cbd5e1', fontSize: 13 }}>
        {r.catalysts.map((c, i) => (
          <li key={i} style={{ marginBottom: 6 }}>
            <strong>{c.name}</strong>{' '}
            <span style={{ color: '#94a3b8' }}>· {c.timing} ·</span>{' '}
            <span
              style={{
                color:
                  c.direction === 'Positive'
                    ? '#4ade80'
                    : c.direction === 'Negative'
                    ? '#f87171'
                    : '#fbbf24',
              }}
            >
              {c.direction}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

// ----------------------------------------------------------- Show work ---
function ShowWorkPanel({ d }: { d: EtvData }) {
  const [open, setOpen] = useState(false)
  const log = d.pipeline_log ?? []

  const totalMs = log.reduce((s, e) => s + (e.latency_ms ?? 0), 0)
  const totalRetries = log.reduce((s, e) => s + (e.retries ?? 0), 0)
  const s5 = log.find((e) => e.stage === 'S5_critic')
  const verdict =
    (s5?.extra?.overall_verdict as string | undefined) ?? '—'
  const summary =
    log.length === 0
      ? 'monolithic run — no staged log'
      : `${log.length} stages · ${(totalMs / 1000).toFixed(1)}s · ${totalRetries > 0 ? `${totalRetries} retry` : 'no retries'} · critic=${verdict}`

  const scenarios: Array<{ key: 'bear' | 'base' | 'bull'; s: EtvScenario }> = [
    { key: 'bear', s: d.report.economic_value.bear },
    { key: 'base', s: d.report.economic_value.base },
    { key: 'bull', s: d.report.economic_value.bull },
  ]

  return (
    <section
      style={{
        background: '#0f172a',
        border: '1px solid #334155',
        borderRadius: 8,
        padding: '10px 14px',
        marginBottom: 14,
        fontSize: 12,
        color: '#cbd5e1',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          background: 'transparent',
          border: 0,
          padding: 0,
          color: '#60a5fa',
          cursor: 'pointer',
          fontSize: 12,
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: 0.6,
          display: 'flex',
          gap: 10,
          alignItems: 'center',
          width: '100%',
          textAlign: 'left',
        }}
      >
        <span>{open ? '▾' : '▸'} Show work</span>
        <span style={{ color: '#94a3b8', fontWeight: 500 }}>{summary}</span>
      </button>

      {open && log.length === 0 && (
        <div style={{ marginTop: 12, color: '#94a3b8' }}>
          Staged pipeline did not run for this report (monolithic fallback).
        </div>
      )}

      {open && log.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Label>Pipeline log</Label>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              marginTop: 4,
              fontSize: 12,
            }}
          >
            <thead style={{ color: '#94a3b8' }}>
              <tr>
                <th style={thLeft}>Stage</th>
                <th style={thRight}>Latency</th>
                <th style={thRight}>Retries</th>
                <th style={thLeft}>Guard</th>
                <th style={thLeft}>Notes</th>
              </tr>
            </thead>
            <tbody>
              {log.map((e, i) => (
                <PipelineRow key={`${e.stage}-${i}`} entry={e} />
              ))}
            </tbody>
          </table>

          <div style={{ marginTop: 14 }}>
            <Label>Per-scenario derivation</Label>
            {scenarios.map(({ key, s }) => {
              const lines = s.derivation
              if (!lines || lines.length === 0) return null
              return (
                <div key={key} style={{ marginTop: 8 }}>
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      color: SCENARIO_COLORS[key],
                      marginBottom: 4,
                      textTransform: 'capitalize',
                    }}
                  >
                    {key} → {fmtCur(s.price)}
                  </div>
                  <ol
                    style={{
                      margin: 0,
                      paddingLeft: 18,
                      color: '#cbd5e1',
                      fontFamily:
                        'ui-monospace, SFMono-Regular, Menlo, monospace',
                      fontSize: 11,
                      lineHeight: 1.55,
                    }}
                  >
                    {lines.map((l, i) => (
                      <li key={i}>{l}</li>
                    ))}
                  </ol>
                </div>
              )
            })}
          </div>

          {d.report.missing_inputs && d.report.missing_inputs.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <Label>
                Missing inputs / assumptions ({d.report.missing_inputs.length})
              </Label>
              <Bullets items={d.report.missing_inputs} />
            </div>
          )}
        </div>
      )}
    </section>
  )
}

const thLeft: React.CSSProperties = {
  textAlign: 'left',
  padding: '4px 6px',
  borderBottom: '1px solid #334155',
  fontWeight: 600,
  textTransform: 'uppercase',
  fontSize: 10,
  letterSpacing: 0.5,
}
const thRight: React.CSSProperties = { ...thLeft, textAlign: 'right' }
const td: React.CSSProperties = {
  padding: '4px 6px',
  borderBottom: '1px solid #1e293b',
  verticalAlign: 'top',
}
const tdRight: React.CSSProperties = { ...td, textAlign: 'right' }

function PipelineRow({ entry }: { entry: EtvPipelineLogEntry }) {
  const g = entry.guard
  const guardText = g
    ? `${g.passed ? '✓' : '✗'} ${g.total_numbers} nums · ` +
      `${g.grounded_count}g/${g.derived_count}d/${g.passthrough_count}p` +
      (g.unjustified.length > 0
        ? ` · ${g.unjustified.length} unjustified`
        : '')
    : '—'
  const notes: string[] = []
  if (entry.extra) {
    for (const [k, v] of Object.entries(entry.extra)) {
      notes.push(`${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    }
  }
  if (entry.reason) notes.push(`reason=${entry.reason}`)
  return (
    <tr>
      <td style={td}>
        <span style={{ color: '#e2e8f0', fontWeight: 600 }}>{entry.stage}</span>
      </td>
      <td style={tdRight}>{(entry.latency_ms / 1000).toFixed(2)}s</td>
      <td style={tdRight}>
        {entry.retries > 0 ? (
          <span style={{ color: '#fbbf24' }}>{entry.retries}</span>
        ) : (
          <span style={{ color: '#64748b' }}>0</span>
        )}
      </td>
      <td style={td}>
        <span style={{ color: g?.passed === false ? '#f87171' : '#94a3b8' }}>
          {guardText}
        </span>
      </td>
      <td style={td}>
        <span style={{ color: '#94a3b8' }}>{notes.join(' · ') || '—'}</span>
      </td>
    </tr>
  )
}

// ================================================================ MAIN ===
export function EtvView() {
  const { data, loading, error, fetchTicker } = useEtv()

  return (
    <div style={{ padding: '0 4px' }}>
      <EtvInputPanel
        loading={loading}
        onRun={(t, h, r, refresh) => fetchTicker(t, h, r, refresh)}
      />

      {error && (
        <div
          style={{
            background: '#7f1d1d22',
            border: '1px solid #7f1d1d',
            color: '#fca5a5',
            padding: '10px 14px',
            borderRadius: 6,
            marginBottom: 14,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {loading && !data && (
        <div style={{ color: '#94a3b8', padding: 14, textAlign: 'center' }}>
          Running multi-layer ETV analysis (may take 30–60 s)…
        </div>
      )}

      {data && (
        <>
          <DecisionBanner d={data} />
          <HeaderCard d={data} />
          {data.report.validation &&
            (data.report.validation.corrections.length > 0 ||
              data.report.validation.warnings.length > 0) && (
              <div
                style={{
                  background: '#0f172a',
                  border: `1px solid ${data.report.validation.passed ? '#334155' : '#b91c1c'}`,
                  borderRadius: 8,
                  padding: '10px 14px',
                  marginBottom: 12,
                  fontSize: 12,
                  color: '#cbd5e1',
                }}
              >
                <div style={{ fontWeight: 700, color: '#60a5fa', marginBottom: 6 }}>
                  Deterministic validator{' '}
                  <span style={{ color: '#94a3b8', fontWeight: 500 }}>
                    ({data.report.validation.passed ? 'passed' : 'warnings'})
                  </span>
                </div>
                {data.report.validation.corrections.length > 0 && (
                  <div style={{ marginBottom: 4 }}>
                    <Label>Corrections applied</Label>
                    <Bullets items={data.report.validation.corrections} />
                  </div>
                )}
                {data.report.validation.warnings.length > 0 && (
                  <div>
                    <Label>Warnings</Label>
                    <Bullets items={data.report.validation.warnings} />
                  </div>
                )}
              </div>
            )}

          <ShowWorkPanel d={data} />

          {/* §1 */}
          <Card step="§1" title="Company summary">
            <div style={{ fontSize: 14, color: '#cbd5e1', lineHeight: 1.5 }}>
              {data.report.company_summary}
            </div>
            {data.report.missing_inputs?.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <Label>Missing inputs / assumptions used</Label>
                <Bullets items={data.report.missing_inputs} />
              </div>
            )}
          </Card>

          <ModelSelectionSection r={data.report} />
          <EconomicValueSection r={data.report} />
          <OptionalitySection r={data.report} />
          <MarketImpliedSection r={data.report} />
          <BehaviorSection r={data.report} />
          <RegimeSection r={data.report} />
          <EtvSection r={data.report} />
          <RiskSection r={data.report} />
          <AsymmetrySection r={data.report} />
          <DecisionSection r={data.report} />
          <SizingSection r={data.report} />
          <CatalystsSection r={data.report} />

          {/* §14 */}
          <Card step="§14" title="Key failure conditions">
            <Bullets items={data.report.failure_conditions} />
          </Card>

          {/* §15 */}
          <Card step="§15" title="Core investment thesis">
            <Bullets items={data.report.core_thesis} />
          </Card>

          <Card title="Adversarial challenges considered">
            <Bullets items={data.report.advisor_challenges} />
          </Card>
        </>
      )}
    </div>
  )
}

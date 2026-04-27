import { useEffect, useMemo, useState } from 'react'
import { useDcf } from '../hooks/useDcf'
import type {
  DcfData,
  ScenarioAssumption,
  ScenarioResult,
  AssumptionKey,
  MonteCarloResult,
  ReverseDcfResult,
  SensitivityMatrix,
  Verdict,
  WaccBuildup,
  Recommendation,
  MultiplesCrossCheck,
  FranchiseFlag,
  HorizonComparison,
} from '../types/dcf'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface ModelConstants {
  equity_risk_premium: number
  default_risk_free: number
  live_risk_free: number | null
  default_pretax_cost_of_debt: number
  min_wacc: number
  max_wacc: number
  mc_trials_default: number
  high_growth_threshold: number
  forecast_years: number
  forecast_years_high_growth: number
  last_reviewed: string
  erp_source: string
  rf_source: string
  kd_source: string
}

// ----------------------------------------------------------------- Utils ---
const ASSUMPTION_LABELS: Record<AssumptionKey, string> = {
  revenue_growth: 'Revenue Growth (Y1)',
  operating_margin: 'Operating Margin (Y1)',
  operating_margin_y5: 'Operating Margin (Y5)',
  discount_rate: 'Discount Rate (WACC)',
  terminal_growth: 'Terminal Growth',
  capex_pct_revenue: 'Capex % of Revenue',
}
const ASSUMPTION_ORDER: AssumptionKey[] = [
  'revenue_growth', 'operating_margin', 'operating_margin_y5',
  'discount_rate', 'terminal_growth', 'capex_pct_revenue',
]
const SCENARIO_COLORS: Record<string, string> = {
  Conservative: '#f87171', Base: '#60a5fa', Optimistic: '#4ade80',
}
const REC_COLORS: Record<Recommendation, string> = {
  STRONG_BUY: '#22c55e', BUY: '#4ade80', HOLD: '#fbbf24', AVOID: '#f87171', STRONG_AVOID: '#dc2626',
}

const fmtPct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`
const fmtCur = (v: number) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
const fmtBigCur = (v: number | null) => {
  if (v == null) return '—'
  const a = Math.abs(v)
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  return fmtCur(v)
}
const fmtNum = (v: number | null, d = 2) =>
  v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: d })

// =============================================================== VERDICT ==
function VerdictBanner({ v, price }: { v: Verdict; price: number }) {
  const col = REC_COLORS[v.recommendation]
  return (
    <div
      style={{
        background: `linear-gradient(90deg, ${col}22, #1e293b 60%)`,
        border: `1px solid ${col}66`,
        borderLeft: `5px solid ${col}`,
        borderRadius: 8,
        padding: '14px 18px',
        display: 'grid',
        gridTemplateColumns: 'minmax(180px, auto) 1fr',
        gap: 18,
        alignItems: 'center',
      }}
    >
      <div>
        <div style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.6 }}>Verdict</div>
        <div style={{ fontSize: 26, fontWeight: 800, color: col, letterSpacing: 0.5 }}>
          {v.recommendation.replace('_', ' ')}
        </div>
        <div style={{ fontSize: 12, color: '#cbd5e1', marginTop: 4 }}>
          Confidence: <strong style={{ color: '#e2e8f0' }}>{fmtPct(v.confidence, 0)}</strong>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12 }}>
        <Stat label="Current" value={fmtCur(price)} />
        <Stat label="Entry below" value={fmtCur(v.suggested_entry_price)} highlight={price <= v.suggested_entry_price ? '#4ade80' : undefined} />
        <Stat label="Exit at" value={fmtCur(v.suggested_exit_price)} />
        <Stat
          label="Margin of safety"
          value={fmtPct(v.margin_of_safety_pct, 0)}
          highlight={v.margin_of_safety_pct > 0 ? '#4ade80' : '#f87171'}
        />
        <div style={{ gridColumn: '1 / -1' }}>
          <div style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Watch this
          </div>
          <div style={{ fontSize: 13, color: '#e2e8f0', fontStyle: 'italic' }}>
            “{v.key_assumption_to_monitor}”
          </div>
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 6 }}>
            {v.deterministic ? '⚙ Deterministic verdict' : '○ LLM verdict'}
            {' · data quality '}
            <strong style={{ color: v.data_quality_score >= 0.75 ? '#4ade80' : v.data_quality_score >= 0.5 ? '#fbbf24' : '#f87171' }}>
              {fmtPct(v.data_quality_score, 0)}
            </strong>
            {v.rationale && <> · <span style={{ color: '#94a3b8' }}>{v.rationale}</span></>}
          </div>
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value, highlight }: { label: string; value: string; highlight?: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 14, color: highlight ?? '#e2e8f0', fontWeight: 600 }}>{value}</div>
    </div>
  )
}

// ============================================================== HEADER ==
function HeaderCard({ d }: { d: DcfData }) {
  const g = d.grounding
  return (
    <div
      style={{
        background: '#1e293b', border: '1px solid #334155', borderRadius: 8,
        padding: '14px 18px',
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12,
      }}
    >
      <div style={{ gridColumn: '1 / -1' }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: '#e2e8f0' }}>
          {g.company_name} <span style={{ color: '#94a3b8', fontWeight: 400 }}>· {g.ticker}</span>
        </div>
        <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 2 }}>
          {g.sector ?? '—'} · {g.industry ?? '—'} · as of {g.as_of}
          {d.cached && <span style={{ color: '#4ade80' }}> · cached</span>}
          <span style={{ color: '#64748b' }}> · {d.model}</span>
        </div>
      </div>
      <Stat label="Price" value={fmtCur(g.current_price)} />
      <Stat label="Market Cap" value={fmtBigCur(g.market_cap)} />
      <Stat label="Net Debt" value={fmtBigCur(g.net_debt)} />
      <Stat label="Beta" value={fmtNum(g.beta)} />
      <Stat label="Revenue TTM" value={fmtBigCur(g.revenue_ttm)} />
      <Stat label="5y Rev CAGR" value={g.revenue_cagr_5y == null ? '—' : fmtPct(g.revenue_cagr_5y)} />
      <Stat label="Op Margin TTM" value={g.operating_margin_ttm == null ? '—' : fmtPct(g.operating_margin_ttm)} />
      <Stat label="Gross Margin" value={g.gross_margin_ttm == null ? '—' : fmtPct(g.gross_margin_ttm)} />
      <Stat label="R&D % Rev" value={g.rnd_pct_revenue == null ? '—' : fmtPct(g.rnd_pct_revenue)} />
      <Stat
        label="ROIC"
        value={g.roic_ttm == null ? '—' : fmtPct(g.roic_ttm)}
        highlight={g.roic_ttm == null ? undefined : g.roic_ttm > g.wacc_buildup.wacc * 1.5 ? '#4ade80' : g.roic_ttm < g.wacc_buildup.wacc ? '#f87171' : undefined}
      />
      <Stat
        label="Net Buyback"
        value={g.buyback_yield == null ? '—' : fmtPct(g.buyback_yield)}
        highlight={g.buyback_yield && g.buyback_yield > 0 ? '#4ade80' : g.buyback_yield && g.buyback_yield < 0 ? '#f87171' : undefined}
      />
      <Stat
        label="SBC Dilution"
        value={g.sbc_dilution_yield == null ? '—' : fmtPct(g.sbc_dilution_yield)}
        highlight={g.sbc_dilution_yield && g.sbc_dilution_yield > 0.02 ? '#f87171' : undefined}
      />
      <Stat label="Forward P/E (mkt)" value={g.forward_pe == null ? '—' : `${g.forward_pe.toFixed(1)}x`} />
    </div>
  )
}

// =========================================================== WACC PANEL ==
function WaccPanel({ b }: { b: WaccBuildup }) {
  return (
    <div
      style={{
        background: '#1e293b', border: '1px solid #334155', borderRadius: 8,
        padding: '12px 16px',
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 700, color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 }}>
        WACC Build-up (CAPM)
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10, fontSize: 12 }}>
        <Stat label="Risk-Free (10y)" value={fmtPct(b.risk_free_rate, 2)} />
        <Stat label="ERP" value={fmtPct(b.equity_risk_premium, 2)} />
        <Stat label="Beta" value={fmtNum(b.beta, 2)} />
        <Stat label="Cost of Equity" value={fmtPct(b.cost_of_equity, 2)} />
        <Stat label="After-Tax Kd" value={fmtPct(b.after_tax_cost_of_debt, 2)} />
        <Stat label="Equity Weight" value={fmtPct(b.weight_equity, 1)} />
        <Stat label="Debt Weight" value={fmtPct(b.weight_debt, 1)} />
        <Stat label="WACC" value={fmtPct(b.wacc, 2)} highlight="#fbbf24" />
      </div>
    </div>
  )
}

// ====================================================== REVERSE DCF =====
function ReverseDcfPanel({ r }: { r: ReverseDcfResult }) {
  const implied = r.implied_revenue_growth
  const tone = r.delta_vs_base == null ? '#94a3b8' : r.delta_vs_base > 0.02 ? '#f87171' : r.delta_vs_base < -0.02 ? '#4ade80' : '#fbbf24'
  return (
    <div
      style={{
        background: '#1e293b', border: '1px solid #334155', borderLeft: `4px solid ${tone}`,
        borderRadius: 8, padding: '12px 16px',
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 700, color: tone, textTransform: 'uppercase', letterSpacing: 0.6 }}>
        Reverse DCF · what is the market pricing in?
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginTop: 8 }}>
        <Stat label="Market-Implied Growth" value={implied == null ? '—' : fmtPct(implied)} highlight={tone} />
        <Stat label="Your Base Case" value={fmtPct(r.base_revenue_growth)} />
        <Stat label="Δ" value={r.delta_vs_base == null ? '—' : `${r.delta_vs_base >= 0 ? '+' : ''}${fmtPct(r.delta_vs_base)}`} highlight={tone} />
      </div>
      <div style={{ fontSize: 13, color: '#cbd5e1', marginTop: 10, lineHeight: 1.5 }}>
        {r.interpretation}
      </div>
    </div>
  )
}

// ====================================================== SCENARIO CARDS ==
function ScenarioCards({ d }: { d: DcfData }) {
  const price = d.grounding.current_price
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
      {d.scenarios.map((sc, i) => {
        const sv = d.scenario_values[i]
        const col = SCENARIO_COLORS[sc.label]
        const upPos = sv.upside_pct >= 0
        return (
          <div
            key={sc.label}
            style={{
              background: '#1e293b', border: `1px solid ${col}55`, borderLeft: `4px solid ${col}`,
              borderRadius: 8, padding: '12px 14px',
            }}
          >
            <div style={{ fontSize: 12, color: col, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.6 }}>
              {sc.label}
            </div>
            <div style={{ fontSize: 24, fontWeight: 700, color: '#e2e8f0', marginTop: 4 }}>
              {fmtCur(sv.fair_value_per_share)}
            </div>
            <div style={{ fontSize: 13, color: upPos ? '#4ade80' : '#f87171', marginTop: 2 }}>
              {upPos ? '▲' : '▼'} {fmtPct(Math.abs(sv.upside_pct))} vs {fmtCur(price)}
            </div>
            <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6, lineHeight: 1.45 }}>
              EV {fmtBigCur(sv.enterprise_value)} · Eq {fmtBigCur(sv.equity_value)}<br />
              PV(FCF) {fmtBigCur(sv.pv_of_fcfs)} · PV(TV) {fmtBigCur(sv.pv_of_terminal)}
            </div>
            <div style={{ marginTop: 8, fontSize: 11, color: '#cbd5e1' }}>
              <span style={{ color: '#94a3b8' }}>Strongest driver: </span>
              <span style={{ color: col, fontWeight: 600 }}>{ASSUMPTION_LABELS[sc.strongest_driver]}</span>
            </div>
            <div style={{ marginTop: 4, fontSize: 10, color: '#64748b' }}>
              WACC adj: {sc.wacc_risk_adj_bps >= 0 ? '+' : ''}{sc.wacc_risk_adj_bps}bp → {fmtPct(sc.discount_rate, 2)}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ============================================ HISTORICAL METRICS ===
function HistoricalPanel({ rows }: {
  rows: Array<{ year: number; revenue_growth: number | null; operating_margin: number | null; capex_pct_revenue: number | null }>
}) {
  if (!rows.length) return null
  const fmt = (v: number | null, d = 1) => v == null ? '—' : `${(v * 100).toFixed(d)}%`
  const tone = (v: number | null, baseline: number, neg = false) => {
    if (v == null) return '#94a3b8'
    const diff = v - baseline
    return diff > 0 ? (neg ? '#f87171' : '#4ade80') : diff < 0 ? (neg ? '#4ade80' : '#f87171') : '#e2e8f0'
  }
  // Compute simple averages for at-a-glance baselines
  const avg = (k: 'revenue_growth' | 'operating_margin' | 'capex_pct_revenue') => {
    const xs = rows.map(r => r[k]).filter((x): x is number => x != null)
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0
  }
  const avgGrowth = avg('revenue_growth')
  const avgMargin = avg('operating_margin')
  const avgCapex = avg('capex_pct_revenue')
  return (
    <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '12px 16px' }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 }}>
        Historical · {rows.length}-yr context
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: '#94a3b8' }}>
              <th style={{ ...th, textAlign: 'left' }}>Metric</th>
              {rows.map((r) => (
                <th key={r.year} style={{ ...th, textAlign: 'right' }}>{r.year}</th>
              ))}
              <th style={{ ...th, textAlign: 'right', color: '#fbbf24' }}>Avg</th>
            </tr>
          </thead>
          <tbody>
            <tr style={{ borderTop: '1px solid #334155' }}>
              <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>Revenue Growth</td>
              {rows.map((r) => (
                <td key={r.year} style={{ ...td, textAlign: 'right', color: tone(r.revenue_growth, avgGrowth) }}>
                  {fmt(r.revenue_growth)}
                </td>
              ))}
              <td style={{ ...td, textAlign: 'right', color: '#fbbf24', fontWeight: 700 }}>{fmt(avgGrowth)}</td>
            </tr>
            <tr style={{ borderTop: '1px solid #334155' }}>
              <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>Operating Margin</td>
              {rows.map((r) => (
                <td key={r.year} style={{ ...td, textAlign: 'right', color: tone(r.operating_margin, avgMargin) }}>
                  {fmt(r.operating_margin)}
                </td>
              ))}
              <td style={{ ...td, textAlign: 'right', color: '#fbbf24', fontWeight: 700 }}>{fmt(avgMargin)}</td>
            </tr>
            <tr style={{ borderTop: '1px solid #334155' }}>
              <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>Capex / Revenue</td>
              {rows.map((r) => (
                <td key={r.year} style={{ ...td, textAlign: 'right', color: tone(r.capex_pct_revenue, avgCapex, true) }}>
                  {fmt(r.capex_pct_revenue)}
                </td>
              ))}
              <td style={{ ...td, textAlign: 'right', color: '#fbbf24', fontWeight: 700 }}>{fmt(avgCapex)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 6 }}>
        Color: green = above {rows.length}-yr average (red for capex \u2014 lower reinvestment is better).
      </div>
    </div>
  )
}

// ============================================ HORIZON COMPARISON ===
const HORIZON_REFERENCE_ROWS: Array<{ pattern: string; reading: string; tone: string }> = [
  { pattern: '|Δ| < 5%', reading: 'Mature business; horizon irrelevant. Perpetuity assumption dominates.', tone: '#94a3b8' },
  { pattern: '5–20% (10y > 5y)', reading: 'Modest runway. Some growth left in Y6–Y10 the 5y model truncates.', tone: '#4ade80' },
  { pattern: '> 20% (10y > 5y)', reading: 'Significant runway value. Market may be pricing future cash flows the 5y model ignores. 10y read better captures bull thesis.', tone: '#22c55e' },
  { pattern: '5y > 10y by 10%+', reading: 'Negative runway: Y6–Y10 fade. Cyclical signature or peak-margin exposure. Be wary of base-case extrapolation.', tone: '#f87171' },
]

function HorizonPanel({ h, currentPrice }: { h: HorizonComparison; currentPrice: number }) {
  const fmt = (v: number) => `$${v.toFixed(2)}`
  const fmtP = (v: number, d = 0) => `${(v * 100).toFixed(d)}%`
  const runwayTone = h.runway_value_pct > 0.20 ? '#22c55e'
    : h.runway_value_pct > 0.05 ? '#4ade80'
    : h.runway_value_pct < -0.10 ? '#f87171'
    : '#94a3b8'
  const cellStyle: React.CSSProperties = { padding: '8px 10px', textAlign: 'right' }
  return (
    <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '12px 16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: 0.6 }}>
          Horizon comparison · 5y vs 10y
        </div>
        <div style={{ fontSize: 11, color: '#94a3b8' }}>
          Primary used in main panels: <strong style={{ color: '#fbbf24' }}>{h.primary_horizon}y</strong>
        </div>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ color: '#94a3b8', fontSize: 11, textTransform: 'uppercase' }}>
            <th style={{ ...th, textAlign: 'left' }}>Metric</th>
            <th style={{ ...th, textAlign: 'right' }}>5-year</th>
            <th style={{ ...th, textAlign: 'right' }}>10-year</th>
            <th style={{ ...th, textAlign: 'right' }}>Δ</th>
          </tr>
        </thead>
        <tbody>
          <tr style={{ borderTop: '1px solid #334155' }}>
            <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>Base FV</td>
            <td style={{ ...cellStyle, color: '#e2e8f0' }}>{fmt(h.horizon_5y.base_fair_value)}</td>
            <td style={{ ...cellStyle, color: '#e2e8f0' }}>{fmt(h.horizon_10y.base_fair_value)}</td>
            <td style={{ ...cellStyle, color: runwayTone, fontWeight: 700 }}>
              {h.runway_value_pct >= 0 ? '+' : ''}{fmtP(h.runway_value_pct, 1)}
            </td>
          </tr>
          <tr style={{ borderTop: '1px solid #334155' }}>
            <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>P50 (MC median)</td>
            <td style={{ ...cellStyle, color: '#e2e8f0' }}>{fmt(h.horizon_5y.p50)}</td>
            <td style={{ ...cellStyle, color: '#e2e8f0' }}>{fmt(h.horizon_10y.p50)}</td>
            <td style={{ ...cellStyle, color: '#94a3b8' }}>—</td>
          </tr>
          <tr style={{ borderTop: '1px solid #334155' }}>
            <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>TV concentration</td>
            <td style={{ ...cellStyle, color: '#e2e8f0' }}>{fmtP(h.horizon_5y.tv_concentration)}</td>
            <td style={{ ...cellStyle, color: '#e2e8f0' }}>{fmtP(h.horizon_10y.tv_concentration)}</td>
            <td style={{ ...cellStyle, color: h.tv_concentration_delta > 0.10 ? '#4ade80' : '#94a3b8' }}>
              {h.tv_concentration_delta >= 0 ? '+' : ''}{(h.tv_concentration_delta * 100).toFixed(1)}pp
            </td>
          </tr>
          <tr style={{ borderTop: '1px solid #334155' }}>
            <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>P(FV &gt; price)</td>
            <td style={{ ...cellStyle, color: h.horizon_5y.prob_above_current >= 0.5 ? '#4ade80' : '#f87171' }}>
              {fmtP(h.horizon_5y.prob_above_current)}
            </td>
            <td style={{ ...cellStyle, color: h.horizon_10y.prob_above_current >= 0.5 ? '#4ade80' : '#f87171' }}>
              {fmtP(h.horizon_10y.prob_above_current)}
            </td>
            <td style={{ ...cellStyle, color: '#94a3b8' }}>vs {fmt(currentPrice)}</td>
          </tr>
        </tbody>
      </table>
      <div style={{ fontSize: 12, color: '#cbd5e1', marginTop: 10, lineHeight: 1.5, padding: '8px 10px', background: '#0f172a', borderRadius: 4 }}>
        {h.diagnostic}
      </div>
      {/* Reference table: how to read the runway delta */}
      <details style={{ marginTop: 8 }}>
        <summary style={{ cursor: 'pointer', fontSize: 11, color: '#94a3b8' }}>
          How to read the 5y vs 10y delta
        </summary>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginTop: 6 }}>
          <thead>
            <tr style={{ color: '#94a3b8', fontSize: 10, textTransform: 'uppercase' }}>
              <th style={{ ...th, textAlign: 'left' }}>Pattern</th>
              <th style={{ ...th, textAlign: 'left' }}>Interpretation</th>
            </tr>
          </thead>
          <tbody>
            {HORIZON_REFERENCE_ROWS.map((r) => (
              <tr key={r.pattern} style={{ borderTop: '1px solid #334155' }}>
                <td style={{ ...td, color: r.tone, fontWeight: 600, whiteSpace: 'nowrap' }}>{r.pattern}</td>
                <td style={{ ...td, color: '#cbd5e1' }}>{r.reading}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  )
}

// ===================================================== ASSUMPTIONS TABLE
function AssumptionsTable({ scenarios, forecastYears }: { scenarios: ScenarioAssumption[]; forecastYears: number }) {
  const showMidGrowth = forecastYears >= 10
  return (
    <div style={{ overflowX: 'auto' }}>
      <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>
        Forecast horizon: <strong style={{ color: '#fbbf24' }}>{forecastYears} years</strong>
        {forecastYears === 10 && <span> (high-growth: two-stage fade with mid-period growth)</span>}
      </div>
      <table style={{
        width: '100%', borderCollapse: 'collapse', fontSize: 13,
        background: '#1e293b', border: '1px solid #334155', borderRadius: 8,
      }}>
        <thead>
          <tr style={{ background: '#0f172a', color: '#94a3b8' }}>
            <th style={th}>Assumption</th>
            {scenarios.map((s) => (
              <th key={s.label} style={{ ...th, color: SCENARIO_COLORS[s.label] }}>{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ASSUMPTION_ORDER.map((key) => (
            <tr key={key} style={{ borderTop: '1px solid #334155' }}>
              <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>{ASSUMPTION_LABELS[key]}</td>
              {scenarios.map((s) => {
                const isStrongest = s.strongest_driver === key
                const v = s[key]
                return (
                  <td key={s.label} style={{
                    ...td,
                    color: isStrongest ? SCENARIO_COLORS[s.label] : '#e2e8f0',
                    fontWeight: isStrongest ? 700 : 500,
                  }} title={s.rationale[key]}>
                    {fmtPct(v, key === 'terminal_growth' ? 2 : 1)}
                    {isStrongest && (
                      <span style={{
                        marginLeft: 6, fontSize: 10,
                        background: SCENARIO_COLORS[s.label] + '33', color: SCENARIO_COLORS[s.label],
                        padding: '1px 5px', borderRadius: 4,
                      }}>★ driver</span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
          {showMidGrowth && (
            <tr style={{ borderTop: '1px solid #334155' }}>
              <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>Mid Growth (Y6–Y10)</td>
              {scenarios.map((s) => (
                <td key={s.label} style={{ ...td, color: '#e2e8f0' }}>
                  {fmtPct(s.mid_growth, 1)}
                </td>
              ))}
            </tr>
          )}
        </tbody>
      </table>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>Hover any cell for the rationale.</div>
    </div>
  )
}

const th: React.CSSProperties = { textAlign: 'left', padding: '8px 12px', fontWeight: 600, fontSize: 12 }
const td: React.CSSProperties = { padding: '10px 12px' }

// ============================================== SENSITIVITY HEATMAP ===
function SensitivityHeatmap({ s, currentPrice }: { s: SensitivityMatrix; currentPrice: number }) {
  const flat = s.grid.flat()
  const min = Math.min(...flat)
  const max = Math.max(...flat)
  const colorFor = (fv: number) => {
    if (max === min) return '#334155'
    const t = (fv - min) / (max - min) // 0..1
    // Red (low) -> Yellow -> Green (high)
    if (t < 0.5) {
      const u = t / 0.5
      return `rgb(${Math.round(248 - u * 100)}, ${Math.round(113 + u * 100)}, ${Math.round(113 - u * 50)})`
    }
    const u = (t - 0.5) / 0.5
    return `rgb(${Math.round(148 - u * 90)}, ${Math.round(213 + u * 20)}, ${Math.round(63 + u * 50)})`
  }
  return (
    <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '12px 16px' }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 }}>
        Sensitivity · WACC × Terminal Growth (Base case)
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              <th style={{ padding: 6, color: '#94a3b8' }}>WACC ↓ / TG →</th>
              {s.terminal_growth_axis.map((tg) => (
                <th key={tg} style={{ padding: 6, color: Math.abs(tg - s.base_terminal_growth) < 1e-9 ? '#fbbf24' : '#94a3b8' }}>
                  {fmtPct(tg, 2)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {s.grid.map((row, i) => {
              const w = s.wacc_axis[i]
              const isBaseW = Math.abs(w - s.base_wacc) < 1e-9
              return (
                <tr key={i}>
                  <td style={{ padding: 6, color: isBaseW ? '#fbbf24' : '#94a3b8', fontWeight: isBaseW ? 700 : 400 }}>
                    {fmtPct(w, 2)}
                  </td>
                  {row.map((fv, j) => {
                    const isBaseTg = Math.abs(s.terminal_growth_axis[j] - s.base_terminal_growth) < 1e-9
                    const center = isBaseW && isBaseTg
                    const above = fv > currentPrice
                    return (
                      <td
                        key={j}
                        style={{
                          padding: '6px 10px',
                          background: colorFor(fv),
                          color: '#0f172a',
                          fontWeight: center ? 800 : 600,
                          border: center ? '2px solid #fbbf24' : '1px solid #1e293b',
                          textAlign: 'right',
                          minWidth: 70,
                        }}
                        title={`WACC ${fmtPct(w, 2)}, TG ${fmtPct(s.terminal_growth_axis[j], 2)} → ${fmtCur(fv)} (${above ? 'above' : 'below'} price)`}
                      >
                        {fmtCur(fv)}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 6 }}>
        Yellow border = base case. Cell color: red → green by fair value. Current price: {fmtCur(currentPrice)}.
      </div>
    </div>
  )
}

// ===================================================== MONTE CARLO ====
function MonteCarloPanel({ mc, currentPrice }: { mc: MonteCarloResult; currentPrice: number }) {
  const { bin_edges, counts } = mc.histogram
  const maxCount = Math.max(...counts, 1)
  const W = 720, H = 220, padL = 40, padR = 16, padT = 12, padB = 28
  const innerW = W - padL - padR, innerH = H - padT - padB
  const xMin = bin_edges[0], xMax = bin_edges[bin_edges.length - 1]
  const xScale = (x: number) => padL + ((x - xMin) / (xMax - xMin || 1)) * innerW
  const yScale = (c: number) => padT + innerH - (c / maxCount) * innerH
  const priceX = currentPrice >= xMin && currentPrice <= xMax ? xScale(currentPrice) : null
  const pcts = mc.percentiles
  return (
    <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '14px 16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: '#e2e8f0' }}>
          Monte Carlo · <span style={{ color: '#94a3b8', fontWeight: 400 }}>{mc.trials.toLocaleString()} trials</span>
        </div>
        <div style={{ fontSize: 12, color: '#cbd5e1' }}>
          P(FV &gt; {fmtCur(currentPrice)}):{' '}
          <span style={{ color: mc.prob_above_current >= 0.5 ? '#4ade80' : '#f87171', fontWeight: 700 }}>
            {fmtPct(mc.prob_above_current, 0)}
          </span>
        </div>
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block' }}>
        {counts.map((c, i) => {
          const x0 = xScale(bin_edges[i]), x1 = xScale(bin_edges[i + 1])
          const y0 = yScale(c), y1 = padT + innerH
          return <rect key={i} x={x0 + 0.5} y={y0} width={Math.max(0, x1 - x0 - 1)} height={Math.max(0, y1 - y0)} fill="#60a5fa" opacity={0.75} />
        })}
        {(['p25', 'p50', 'p75'] as const).map((k) => {
          const v = pcts[k]
          if (v < xMin || v > xMax) return null
          const x = xScale(v)
          return (
            <g key={k}>
              <line x1={x} x2={x} y1={padT} y2={padT + innerH} stroke="#fbbf24" strokeDasharray="4 3" strokeWidth={1} />
              <text x={x + 3} y={padT + 10} fill="#fbbf24" fontSize="10">{k.toUpperCase()} {fmtCur(v)}</text>
            </g>
          )
        })}
        {priceX != null && (
          <g>
            <line x1={priceX} x2={priceX} y1={padT} y2={padT + innerH} stroke="#f87171" strokeWidth={2} />
            <text x={priceX + 3} y={padT + innerH - 4} fill="#f87171" fontSize="10">price {fmtCur(currentPrice)}</text>
          </g>
        )}
        <text x={padL} y={H - 8} fill="#94a3b8" fontSize="10">{fmtCur(xMin)}</text>
        <text x={W - padR} y={H - 8} fill="#94a3b8" fontSize="10" textAnchor="end">{fmtCur(xMax)}</text>
      </svg>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8, marginTop: 12, fontSize: 12 }}>
        {(['p25', 'p40', 'p50', 'p60', 'p75'] as const).map((k) => (
          <div key={k} style={{ background: '#0f172a', borderRadius: 6, padding: '6px 8px', textAlign: 'center' }}>
            <div style={{ fontSize: 10, color: '#94a3b8', textTransform: 'uppercase' }}>{k}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>{fmtCur(pcts[k])}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 8 }}>
        mean {fmtCur(mc.mean)} · stdev {fmtCur(mc.std)}
      </div>
    </div>
  )
}

// ============================================================ NARRATIVE ==
function Narratives({ scenarios }: { scenarios: ScenarioAssumption[] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {scenarios.map((s) => (
        <details key={s.label} style={{
          background: '#1e293b',
          border: `1px solid ${SCENARIO_COLORS[s.label]}33`,
          borderLeft: `3px solid ${SCENARIO_COLORS[s.label]}`,
          borderRadius: 6, padding: '8px 12px',
        }}>
          <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600, color: SCENARIO_COLORS[s.label] }}>
            {s.label} — narrative
          </summary>
          <p style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.55, marginTop: 8, marginBottom: 0 }}>
            {s.narrative}
          </p>
        </details>
      ))}
    </div>
  )
}

function RisksAndDrivers({ d }: { d: DcfData }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 10 }}>
      <Bulletbox title="Key Value Drivers" items={d.key_drivers} accent="#4ade80" />
      <Bulletbox title="Risks" items={d.risks} accent="#f87171" />
    </div>
  )
}

// ============================================== MULTIPLES CROSS-CHECK ==
function MultiplesPanel({ m }: { m: MultiplesCrossCheck }) {
  const rows: Array<{ label: string; implied: number | null; market: number | null; delta: number | null; suffix: string }> = [
    { label: 'Forward P/E', implied: m.implied_forward_pe, market: m.market_forward_pe, delta: m.pe_delta_pct, suffix: 'x' },
    { label: 'EV / EBITDA', implied: m.implied_ev_ebitda, market: m.market_ev_ebitda, delta: m.ev_ebitda_delta_pct, suffix: 'x' },
    { label: 'EV / Revenue', implied: m.implied_ev_revenue, market: m.market_ev_revenue, delta: m.ev_revenue_delta_pct, suffix: 'x' },
  ]
  const fmtMul = (v: number | null, s: string) => v == null ? '—' : `${v.toFixed(1)}${s}`
  const deltaTone = (d: number | null) =>
    d == null ? '#94a3b8' : Math.abs(d) < 0.15 ? '#4ade80' : Math.abs(d) < 0.30 ? '#fbbf24' : '#f87171'
  return (
    <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '12px 16px' }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 }}>
        Multiples Cross-Check · DCF vs Market
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ color: '#94a3b8', fontSize: 11, textTransform: 'uppercase' }}>
            <th style={{ ...th, textAlign: 'left' }}>Metric</th>
            <th style={{ ...th, textAlign: 'right' }}>DCF Implied</th>
            <th style={{ ...th, textAlign: 'right' }}>Market</th>
            <th style={{ ...th, textAlign: 'right' }}>Δ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} style={{ borderTop: '1px solid #334155' }}>
              <td style={{ ...td, color: '#cbd5e1', fontWeight: 600 }}>{r.label}</td>
              <td style={{ ...td, textAlign: 'right', color: '#e2e8f0' }}>{fmtMul(r.implied, r.suffix)}</td>
              <td style={{ ...td, textAlign: 'right', color: '#e2e8f0' }}>{fmtMul(r.market, r.suffix)}</td>
              <td style={{ ...td, textAlign: 'right', color: deltaTone(r.delta), fontWeight: 700 }}>
                {r.delta == null ? '—' : `${r.delta >= 0 ? '+' : ''}${(r.delta * 100).toFixed(0)}%`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontSize: 12, color: '#cbd5e1', marginTop: 10, lineHeight: 1.5 }}>
        {m.diagnostic}
      </div>
    </div>
  )
}

// ================================================ FRANCHISE BANNER ==
function FranchiseBanner({ f }: { f: FranchiseFlag }) {
  const tone = f.is_franchise ? '#fbbf24' : (f.roic != null && f.roic < f.wacc ? '#f87171' : '#475569')
  return (
    <div style={{
      background: f.is_franchise ? '#3f2f0a' : '#1e293b',
      border: `1px solid ${tone}66`,
      borderLeft: `4px solid ${tone}`,
      borderRadius: 6, padding: '10px 14px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: tone, textTransform: 'uppercase', letterSpacing: 0.6 }}>
          {f.is_franchise ? '★ Franchise flag' : 'ROIC vs WACC'}
        </div>
        <div style={{ fontSize: 12, color: '#cbd5e1' }}>
          ROIC <strong>{f.roic == null ? '—' : fmtPct(f.roic, 0)}</strong>{' '}
          · WACC <strong>{fmtPct(f.wacc, 1)}</strong>{' '}
          {f.spread != null && <>· Spread <strong style={{ color: tone }}>{(f.spread * 100).toFixed(1)}pp</strong></>}
        </div>
      </div>
      <div style={{ fontSize: 12, color: '#cbd5e1', marginTop: 6, lineHeight: 1.5 }}>{f.message}</div>
    </div>
  )
}

function Bulletbox({ title, items, accent }: { title: string; items: string[]; accent: string }) {
  return (
    <div style={{
      background: '#1e293b', border: '1px solid #334155', borderLeft: `3px solid ${accent}`,
      borderRadius: 6, padding: '10px 14px',
    }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: accent, textTransform: 'uppercase', letterSpacing: 0.6 }}>{title}</div>
      <ul style={{ margin: '8px 0 0 0', paddingLeft: 18, color: '#cbd5e1', fontSize: 13, lineHeight: 1.55 }}>
        {items.map((it, i) => (<li key={i}>{it}</li>))}
      </ul>
    </div>
  )
}

// =============================================================== MAIN ====
function ConstantsBanner({ c }: { c: ModelConstants }) {
  const fmtP = (v: number, d = 2) => `${(v * 100).toFixed(d)}%`
  const rfDelta = c.live_risk_free != null ? c.live_risk_free - c.default_risk_free : null
  return (
    <details style={{
      background: '#0f172a', border: '1px solid #334155', borderRadius: 6,
      padding: '8px 12px', fontSize: 12,
    }}>
      <summary style={{ cursor: 'pointer', color: '#cbd5e1', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <span style={{ fontWeight: 700, color: '#fbbf24', textTransform: 'uppercase', letterSpacing: 0.6, fontSize: 11 }}>
          Model constants <span style={{ color: '#64748b', fontWeight: 400, textTransform: 'none' }}>· last reviewed {c.last_reviewed}</span>
        </span>
        <span style={{ color: '#94a3b8', fontSize: 11 }}>
          ERP <strong style={{ color: '#e2e8f0' }}>{fmtP(c.equity_risk_premium, 1)}</strong>
          {' · '}rf live{' '}
          <strong style={{ color: c.live_risk_free == null ? '#f87171' : '#4ade80' }}>
            {c.live_risk_free == null ? 'N/A' : fmtP(c.live_risk_free, 2)}
          </strong>
          {' · '}Kd <strong style={{ color: '#e2e8f0' }}>{fmtP(c.default_pretax_cost_of_debt, 1)}</strong>
          {' · '}WACC band <strong style={{ color: '#e2e8f0' }}>{fmtP(c.min_wacc, 0)}–{fmtP(c.max_wacc, 0)}</strong>
        </span>
      </summary>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10, marginTop: 10, color: '#cbd5e1' }}>
        <div>
          <div style={{ fontSize: 10, color: '#94a3b8', textTransform: 'uppercase' }}>Equity Risk Premium</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>{fmtP(c.equity_risk_premium, 1)}</div>
          <div style={{ fontSize: 10, color: '#64748b' }}>{c.erp_source}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#94a3b8', textTransform: 'uppercase' }}>Risk-Free Rate (live)</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: c.live_risk_free == null ? '#f87171' : '#e2e8f0' }}>
            {c.live_risk_free == null ? 'fallback' : fmtP(c.live_risk_free, 2)}
            {rfDelta != null && (
              <span style={{ fontSize: 11, color: '#94a3b8', fontWeight: 400, marginLeft: 6 }}>
                ({rfDelta >= 0 ? '+' : ''}{(rfDelta * 10000).toFixed(0)}bp vs default {fmtP(c.default_risk_free, 1)})
              </span>
            )}
          </div>
          <div style={{ fontSize: 10, color: '#64748b' }}>{c.rf_source}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#94a3b8', textTransform: 'uppercase' }}>Pre-tax Cost of Debt</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>{fmtP(c.default_pretax_cost_of_debt, 1)}</div>
          <div style={{ fontSize: 10, color: '#64748b' }}>{c.kd_source}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#94a3b8', textTransform: 'uppercase' }}>WACC Guardrails</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>{fmtP(c.min_wacc, 0)} – {fmtP(c.max_wacc, 0)}</div>
          <div style={{ fontSize: 10, color: '#64748b' }}>Output clipped to this range</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#94a3b8', textTransform: 'uppercase' }}>High-Growth Trigger</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>{fmtP(c.high_growth_threshold, 0)}</div>
          <div style={{ fontSize: 10, color: '#64748b' }}>5y CAGR above → {c.forecast_years_high_growth}y forecast</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#94a3b8', textTransform: 'uppercase' }}>Monte Carlo Default</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>{c.mc_trials_default.toLocaleString()} trials</div>
          <div style={{ fontSize: 10, color: '#64748b' }}>Configurable 500–20,000</div>
        </div>
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 8, fontStyle: 'italic' }}>
        Review quarterly: ERP (Damodaran site), rf (auto from ^TNX), Kd (vs current BBB spread). Edit `backend/services/dcf_service.py` constants block.
      </div>
    </details>
  )
}

export function DcfView() {
  const [ticker, setTicker] = useState('AAPL')
  const [trials, setTrials] = useState(5000)
  const [constants, setConstants] = useState<ModelConstants | null>(null)
  const { data, loading, error, fetchTicker } = useDcf()

  useEffect(() => {
    fetch(`${API_BASE}/api/dcf/constants`)
      .then(r => r.ok ? r.json() : null)
      .then((c: ModelConstants | null) => { if (c) setConstants(c) })
      .catch(() => { /* silent */ })
  }, [])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (ticker.trim()) fetchTicker(ticker.trim().toUpperCase(), false, trials)
  }

  const orderedData = useMemo(() => {
    if (!data) return null
    const order: Record<string, number> = { Conservative: 0, Base: 1, Optimistic: 2 }
    const idxs = data.scenarios.map((s, i) => ({ i, k: order[s.label] ?? 99 })).sort((a, b) => a.k - b.k)
    return {
      ...data,
      scenarios: idxs.map(({ i }) => data.scenarios[i]),
      scenario_values: idxs.map(({ i }) => data.scenario_values[i]),
    } as DcfData & { scenario_values: ScenarioResult[] }
  }, [data])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          type="text" value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="Ticker (e.g. AAPL)"
          className="chip-input"
          style={{ width: 160, padding: '8px 12px', fontSize: 14 }}
          disabled={loading}
        />
        <button type="submit" className="btn btn-primary" disabled={loading || !ticker.trim()}>
          {loading ? 'Valuing…' : '💰 Run DCF'}
        </button>
        {data && (
          <button type="button" className="btn"
            onClick={() => fetchTicker(ticker, true, trials)} disabled={loading}
            style={{ background: '#334155', color: '#cbd5e1' }}
            title="Re-run analysis (skip cache)">
            ↻ Refresh
          </button>
        )}
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#cbd5e1' }}>
          <span>MC trials</span>
          <select
            value={trials}
            onChange={(e) => setTrials(parseInt(e.target.value, 10))}
            disabled={loading}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 4, padding: '4px 6px', fontSize: 12 }}
          >
            <option value={1000}>1,000</option>
            <option value={5000}>5,000</option>
            <option value={10000}>10,000</option>
            <option value={20000}>20,000</option>
          </select>
        </label>
        <span style={{ fontSize: 11, color: '#64748b' }}>
          CAPM WACC · 1000-trial Monte Carlo · reverse DCF · sensitivity grid · not investment advice
        </span>
      </form>

      {constants && <ConstantsBanner c={constants} />}

      {error && (
        <div style={{ padding: '10px 14px', background: '#7f1d1d', color: '#fecaca', borderRadius: 6 }}>⚠ {error}</div>
      )}
      {loading && (
        <div className="loading-state">
          <div className="spinner" />
          <p>Fetching grounding · running CAPM, DCF, reverse DCF, Monte Carlo &mdash; est. <strong>~30s</strong></p>
        </div>
      )}

      {!loading && orderedData && (
        <>
          <VerdictBanner v={orderedData.verdict} price={orderedData.grounding.current_price} />
          <HeaderCard d={orderedData} />
          <FranchiseBanner f={orderedData.franchise_flag} />
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 1fr) minmax(260px, 1fr)', gap: 10 }}>
            <WaccPanel b={orderedData.grounding.wacc_buildup} />
            <ReverseDcfPanel r={orderedData.reverse_dcf} />
          </div>
          <ScenarioCards d={orderedData} />
          <HorizonPanel h={orderedData.horizon_comparison} currentPrice={orderedData.grounding.current_price} />
          <HistoricalPanel rows={orderedData.grounding.historical_metrics} />
          <AssumptionsTable scenarios={orderedData.scenarios} forecastYears={orderedData.forecast_years_used} />
          <MultiplesPanel m={orderedData.multiples} />
          <SensitivityHeatmap s={orderedData.sensitivity} currentPrice={orderedData.grounding.current_price} />
          <MonteCarloPanel mc={orderedData.monte_carlo} currentPrice={orderedData.grounding.current_price} />
          <Narratives scenarios={orderedData.scenarios} />
          <RisksAndDrivers d={orderedData} />
        </>
      )}

      {!loading && !data && !error && (
        <div className="empty-state">
          <p>Enter a ticker and click <strong>💰 Run DCF</strong>. You'll get a verdict, CAPM WACC build-up, reverse DCF, sensitivity grid, and Monte Carlo distribution.</p>
        </div>
      )}
    </div>
  )
}

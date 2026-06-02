/* eslint-disable react-refresh/only-export-components */
// Shared sub-components for the DD Coach wizard: data card panel + hard-rail
// banner + growth-lens block. Kept in one file to avoid an explosion of tiny
// components.
import type { DataCard, GrowthLens, HardRailFlags, YearlyMetric } from '../../types/ddCoach'

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  return `$${v.toFixed(2)}`
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(digits)}%`
}

function fmtMultiple(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v.toFixed(1)}x`
}

export function DataCardPanel({ card }: { card: DataCard }) {
  return (
    <div className="dd-card">
      <header className="dd-card-header">
        <div>
          <h3 className="dd-card-title">{card.company_name ?? card.ticker}</h3>
          <p className="dd-card-subtitle">
            {card.ticker} · {card.sector ?? 'Unknown sector'} · {card.industry ?? '—'}
          </p>
        </div>
        <div className="dd-card-price">
          <div className="dd-card-spot">{fmtMoney(card.spot_price)}</div>
          <div className="dd-card-meta">Market cap {fmtMoney(card.market_cap)}</div>
        </div>
      </header>

      <HardRailBanner flags={card.flags} />

      <div className="dd-card-grid">
        <KeyValue label="Cash on hand" value={fmtMoney(card.cash)} />
        <KeyValue label="Total debt" value={fmtMoney(card.debt)} />
        <KeyValue
          label="Net cash"
          value={fmtMoney(card.net_cash_position)}
          tone={card.net_cash_position != null && card.net_cash_position < 0 ? 'warn' : 'default'}
        />
        <KeyValue label="Price ÷ sales" value={fmtMultiple(card.price_to_sales_ttm)} />
        <KeyValue label="Price ÷ earnings" value={fmtMultiple(card.price_to_earnings_ttm)} />
      </div>

      <SeriesTable label="Revenue (last 3 yrs)" series={card.revenue_3yr} formatter={fmtMoney} ttm={card.revenue_ttm} />
      <SeriesTable label="Free cash flow (last 3 yrs)" series={card.fcf_3yr} formatter={fmtMoney} ttm={card.fcf_ttm} />

      {card.growth_lens && <GrowthLensBlock lens={card.growth_lens} />}
    </div>
  )
}

function HardRailBanner({ flags }: { flags: HardRailFlags }) {
  if (!flags.balance_sheet_red) {
    return (
      <div className="dd-rail dd-rail-ok">
        ✓ The balance sheet looks healthy on the obvious checks.
      </div>
    )
  }
  return (
    <div className="dd-rail dd-rail-warn">
      <strong>Heads up — a few things to read carefully before buying.</strong>
      <ul>
        {flags.reasons.map(r => <li key={r}>{r}</li>)}
      </ul>
    </div>
  )
}

function GrowthLensBlock({ lens }: { lens: GrowthLens }) {
  return (
    <div className="dd-growth-lens">
      <h4>Growth Lens — because this company isn't yet profitable</h4>
      <p className="dd-growth-summary">{lens.summary}</p>
      <div className="dd-card-grid">
        <KeyValue
          label="Cash runway"
          value={lens.cash_runway_years == null ? '—' : `${lens.cash_runway_years.toFixed(1)} yrs`}
        />
        <KeyValue
          label="Share dilution (3yr)"
          value={fmtPct(lens.share_dilution_pct_3yr, 0)}
          tone={lens.share_dilution_pct_3yr != null && lens.share_dilution_pct_3yr > 0.1 ? 'warn' : 'default'}
        />
      </div>
      <SeriesTable
        label="Gross margin trend"
        series={lens.gross_margin_3yr}
        formatter={v => fmtPct(v, 0)}
      />
    </div>
  )
}

function KeyValue({ label, value, tone = 'default' }: {
  label: string
  value: string
  tone?: 'default' | 'warn'
}) {
  return (
    <div className={`dd-kv${tone === 'warn' ? ' dd-kv-warn' : ''}`}>
      <div className="dd-kv-label">{label}</div>
      <div className="dd-kv-value">{value}</div>
    </div>
  )
}

function SeriesTable({ label, series, formatter, ttm }: {
  label: string
  series: YearlyMetric[]
  formatter: (v: number | null) => string
  ttm?: number | null
}) {
  if (series.length === 0 && ttm == null) {
    return (
      <div className="dd-series">
        <div className="dd-series-label">{label}</div>
        <div className="dd-series-empty">No data.</div>
      </div>
    )
  }
  return (
    <div className="dd-series">
      <div className="dd-series-label">{label}</div>
      <div className="dd-series-row">
        {series.map(pt => (
          <div key={pt.year} className="dd-series-cell">
            <div className="dd-series-year">{pt.year}</div>
            <div className="dd-series-value">{formatter(pt.value)}</div>
          </div>
        ))}
        {ttm != null && (
          <div key="ttm" className="dd-series-cell dd-series-cell-ttm">
            <div className="dd-series-year">TTM</div>
            <div className="dd-series-value">{formatter(ttm)}</div>
          </div>
        )}
      </div>
    </div>
  )
}

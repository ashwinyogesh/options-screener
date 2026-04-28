// Header strip showing company name, filing dates, 8-K count, enrichment
// badges, and a partial-corpus warning when ``eight_k_failed_count > 0``.
import type { SupplyChainData } from '../../types/supplyChain'

export function MetadataBar({ data }: { data: SupplyChainData }) {
  const partial = data.eight_k_failed_count > 0
  return (
    <span style={{ fontSize: 12, color: '#94a3b8' }}>
      {data.company_name} · 10-K filed {data.filing_date}
      {data.eight_k_count > 0 && (
        <span title={`8-K dates: ${data.eight_k_dates.join(', ')}`}>
          {' '}
          · +{data.eight_k_count} 8-K{data.eight_k_count > 1 ? 's' : ''}
        </span>
      )}
      {partial && (
        <span
          style={{ color: '#f87171', marginLeft: 4 }}
          title={`${data.eight_k_failed_count} 8-K fetch(es) failed; graph reflects partial corpus.`}
        >
          · ⚠ {data.eight_k_failed_count} failed
        </span>
      )}
      {data.enrichment_used?.includes('industry') && (
        <span style={{ color: '#fbbf24' }}> · +industry</span>
      )}
      {data.cached && <span style={{ color: '#4ade80' }}> · cached</span>}
    </span>
  )
}

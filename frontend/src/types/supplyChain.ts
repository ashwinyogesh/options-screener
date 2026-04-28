export type NodeSource = '10-K' | '8-K' | 'industry'

export interface CompanyNode {
  name: string
  ticker: string | null
  relationship: string
  revenue_pct: number | null
  cost_pct: number | null
  notes: string
  source: NodeSource
  segment: string | null
  confidence: number | null
}

export interface SupplyChainData {
  ticker: string
  company_name: string
  filing_date: string
  accession: string
  suppliers: CompanyNode[]
  customers: CompanyNode[]
  competitors: CompanyNode[]
  summary: string
  cached: boolean
  eight_k_count: number
  eight_k_dates: string[]
  segments: string[]
  concentration_note: string
  enrichment_used: string[]
  eight_k_failed_count: number
}

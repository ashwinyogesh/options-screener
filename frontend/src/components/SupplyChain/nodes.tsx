// React-Flow node label + style helpers. Stays separate from layout.ts
// (which is JSX-free and pure) so the math can be unit-tested without
// pulling in React.
import type { CompanyNode, NodeSource } from '../../types/supplyChain'
import { SourceBadge } from './SourceBadge'

export type NodeKind = 'supplier' | 'customer' | 'competitor'

export function nodeLabel(c: CompanyNode, kind: NodeKind) {
  const pct = kind === 'supplier' ? c.cost_pct : kind === 'customer' ? c.revenue_pct : null
  return (
    <div style={{ padding: '4px 6px', textAlign: 'left' }}>
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: '#f1f5f9',
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <span>
          {c.ticker ? `${c.ticker} ` : ''}
          <span style={{ fontWeight: 400, color: '#94a3b8' }}>{c.name}</span>
        </span>
        <SourceBadge source={c.source} />
      </div>
      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>
        {c.relationship}
        {pct != null && <span style={{ color: '#fbbf24' }}> · {pct.toFixed(1)}%</span>}
      </div>
    </div>
  )
}

export function nodeStyle(borderColor: string, source: NodeSource) {
  const isInferred = source === 'industry'
  return {
    background: '#0f172a',
    border: `1px ${isInferred ? 'dashed' : 'solid'} ${borderColor}`,
    borderRadius: 6,
    width: 200,
    padding: 0,
  }
}

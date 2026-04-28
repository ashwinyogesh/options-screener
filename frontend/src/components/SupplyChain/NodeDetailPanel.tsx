// Right-side detail panel that opens when a non-focal node is clicked.
import type { CompanyNode } from '../../types/supplyChain'
import { SourceBadge } from './SourceBadge'

interface NodeDetailPanelProps {
  selected: CompanyNode
  focalTicker: string
  onClose: () => void
}

export function NodeDetailPanel({ selected, focalTicker, onClose }: NodeDetailPanelProps) {
  return (
    <div
      style={{
        width: 320,
        padding: 14,
        background: '#0f172a',
        borderRadius: 8,
        border: '1px solid #334155',
        overflowY: 'auto',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 10,
        }}
      >
        <div>
          {selected.ticker && (
            <div style={{ fontSize: 16, fontWeight: 700, color: '#fbbf24' }}>{selected.ticker}</div>
          )}
          <div style={{ fontSize: 13, color: '#cbd5e1' }}>{selected.name}</div>
          <div style={{ marginTop: 4 }}>
            <SourceBadge source={selected.source} />
            {selected.segment && (
              <span style={{ fontSize: 10, color: '#94a3b8', marginLeft: 6 }}>
                · {selected.segment}
              </span>
            )}
            {selected.confidence != null && (
              <span style={{ fontSize: 10, color: '#fbbf24', marginLeft: 6 }}>
                · conf {(selected.confidence * 100).toFixed(0)}%
              </span>
            )}
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            color: '#94a3b8',
            border: 'none',
            cursor: 'pointer',
            fontSize: 18,
          }}
          aria-label="Close"
        >
          ×
        </button>
      </div>
      <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 8 }}>
        <strong style={{ color: '#cbd5e1' }}>Relationship:</strong> {selected.relationship}
      </div>
      {selected.revenue_pct != null && (
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
          <strong style={{ color: '#cbd5e1' }}>% of {focalTicker} revenue:</strong>{' '}
          <span style={{ color: '#fbbf24' }}>{selected.revenue_pct.toFixed(1)}%</span>
        </div>
      )}
      {selected.cost_pct != null && (
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
          <strong style={{ color: '#cbd5e1' }}>% of {focalTicker} costs:</strong>{' '}
          <span style={{ color: '#fbbf24' }}>{selected.cost_pct.toFixed(1)}%</span>
        </div>
      )}
      {selected.notes && (
        <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 10, lineHeight: 1.5 }}>
          {selected.notes}
        </div>
      )}
    </div>
  )
}

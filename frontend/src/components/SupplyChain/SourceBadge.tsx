// Tiny presentational component for the source provenance pill that
// appears next to each node label and inside the legend / detail panel.
import type { NodeSource } from '../../types/supplyChain'
import { SOURCE_COLORS } from './layout'

const SOURCE_TITLES: Record<NodeSource, string> = {
  '10-K': 'Disclosed in 10-K filing',
  '8-K': 'Disclosed in 8-K filing',
  industry: 'Inferred from public industry knowledge',
}

export function SourceBadge({ source }: { source: NodeSource }) {
  const color = SOURCE_COLORS[source]
  return (
    <span
      style={{
        display: 'inline-block',
        fontSize: 8,
        fontWeight: 700,
        padding: '1px 4px',
        borderRadius: 3,
        border: `1px solid ${color}`,
        color,
        marginLeft: 6,
        letterSpacing: 0.3,
        textTransform: 'uppercase',
        verticalAlign: 'middle',
      }}
      title={SOURCE_TITLES[source]}
    >
      {source === 'industry' ? 'inf' : source}
    </span>
  )
}

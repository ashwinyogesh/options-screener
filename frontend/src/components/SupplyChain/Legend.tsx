// Floating overlay legend on the top-right of the graph canvas.
import { SourceBadge } from './SourceBadge'

export function Legend() {
  return (
    <div
      style={{
        position: 'absolute',
        top: 10,
        right: 10,
        padding: '8px 10px',
        background: 'rgba(15, 23, 42, 0.92)',
        border: '1px solid #334155',
        borderRadius: 6,
        fontSize: 10,
        color: '#cbd5e1',
        lineHeight: 1.6,
        zIndex: 10,
        minWidth: 150,
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: 4, color: '#f1f5f9' }}>Sources</div>
      <div>
        <SourceBadge source="10-K" /> from 10-K filing
      </div>
      <div>
        <SourceBadge source="8-K" /> from 8-K (recent)
      </div>
      <div>
        <SourceBadge source="industry" /> inferred (LLM)
      </div>
      <div
        style={{
          borderTop: '1px solid #334155',
          marginTop: 5,
          paddingTop: 4,
          fontSize: 9,
          color: '#64748b',
        }}
      >
        Solid edges = disclosed
        <br />
        Dashed edges = inferred
      </div>
    </div>
  )
}

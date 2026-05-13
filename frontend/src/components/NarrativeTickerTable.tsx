import type { AcsScore } from '../types/narrative'

interface NarrativeTickerTableProps {
  rows: AcsScore[]
  emptyMessage: string
}

export function NarrativeTickerTable({ rows, emptyMessage }: NarrativeTickerTableProps) {
  if (rows.length === 0) {
    return <p className="muted">{emptyMessage}</p>
  }
  return (
    <table className="narrative-table">
      <thead>
        <tr>
          <th>Ticker</th>
          <th>ACS</th>
          <th>CI</th>
          <th>A</th>
          <th>B</th>
          <th>C</th>
          <th>D</th>
          <th>E</th>
          <th>Signal</th>
          <th>Flags</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(row => (
          <tr key={`${row.ticker}-${row.scored_at}`}>
            <td><strong>{row.ticker}</strong></td>
            <td>{row.acs.toFixed(1)}</td>
            <td className="muted">{row.acs_ci_lower.toFixed(0)}–{row.acs_ci_upper.toFixed(0)}</td>
            <td>{row.components.a_attention_persistence.toFixed(1)}</td>
            <td>{row.components.b_contributor_quality.toFixed(1)}</td>
            <td>{row.components.c_narrative_strength.toFixed(1)}</td>
            <td>{row.components.d_thesis_quality.toFixed(1)}</td>
            <td>{row.components.e_market_confirmation.toFixed(1)}</td>
            <td>{row.dominant_signal}</td>
            <td className="muted">{row.flags.join(', ') || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * Score legend — explains the A–E components and common flags.
 *
 * Keeps the table itself uncluttered. Links to the methodology doc for the
 * full derivation.
 */

const COMPONENTS = [
  ['A', 'Daily activity (max 25)', 'Has it been discussed consistently over the past 14 days? A high score means organic, sustained interest — not a one-day spike.'],
  ['B', 'Post diversity (max 20)', 'Are many different people posting, or just one account? A high score means broad, independent voices — not a coordinated campaign.'],
  ['C', 'Narrative coherence (max 20)', 'Do the posts share a common theme or thesis? Requires the hourly narrative detector to run — shows 0 until then.'],
  ['D', 'Analytical depth (max 20)', 'What fraction of posts include real analysis (earnings data, valuations, competitive research) vs. pure hype?'],
  ['E', 'Market confirmation (max 15)', 'Is the price and options market starting to reflect the narrative? Not yet live — always 0.'],
] as const

const FLAGS = [
  ['gini_high',          'Concentrated posts — a small number of accounts are responsible for most of the discussion. Check the source posts before acting.'],
  ['decelerating_3d',    'Fading momentum — the mention rate has dropped for 3 consecutive days. The narrative may be cooling.'],
  ['late_stage',         'Late stage — the narrative has passed the ideal entry window (stage > 3).'],
  ['small_cap',          'Small cap — market cap under $100M. Extra caution on liquidity and manipulation risk.'],
  ['low_unique_authors', 'Few voices — not enough distinct people posting yet for reliable signals.'],
] as const

export function ScoreLegend() {
  return (
    <details className="score-legend">
      <summary>How is this scored?</summary>
      <div className="score-legend-body">
        <h4>Score breakdown (A–E, total out of 100)</h4>
        <ul>
          {COMPONENTS.map(([key, name, desc]) => (
            <li key={key}>
              <strong>{key} — {name}:</strong> <span style={{ opacity: 0.8 }}>{desc}</span>
            </li>
          ))}
        </ul>
        <h4>Warnings explained</h4>
        <ul>
          {FLAGS.map(([flag, desc]) => (
            <li key={flag}>
              <strong>{flag.replace(/_/g, ' ')}:</strong> {desc}
            </li>
          ))}
        </ul>
        <p style={{ opacity: 0.7, fontSize: '0.85em' }}>
          Full derivation:{' '}
          <a
            href="https://github.com/ashwincha/Options/blob/main/docs/NARRATIVE_METHODOLOGY.md"
            target="_blank"
            rel="noopener noreferrer"
          >
            NARRATIVE_METHODOLOGY.md
          </a>
        </p>
      </div>
    </details>
  )
}

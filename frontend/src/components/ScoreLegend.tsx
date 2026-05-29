/**
 * Score legend — explains the ACS scoring system in full.
 *
 * Mirrors CspInput / DitmInput SCORE_LEGEND depth for the Narrative tab.
 * Keeps the table itself uncluttered via a <details> collapse.
 */

interface ComponentDef {
  letter: string
  name: string
  max: number
  what: string
  formula: string
  normalization: string
  why: string
}

const COMPONENTS: ComponentDef[] = [
  {
    letter: 'A',
    name: 'Attention persistence',
    max: 30,
    what: 'How consistently has this ticker been discussed over the last 14 days?',
    formula: 'A = min(decay_weighted_density_14d, 1.0) × 30',
    normalization:
      'decay_weighted_density_14d = Σ e^{−0.1·t} × daily_mentions_t / max_possible_weight  (λ=0.1, half-life ≈ 7 days). Capped at 1.0 before multiplying by 25.',
    why:
      'A one-day spike is noise. Persistent daily discussion over two weeks signals that a real thesis is circulating — multiple people independently rediscovering the same idea.',
  },
  {
    letter: 'B',
    name: 'Contributor quality',
    max: 25,
    what: 'Are many different people posting, or just a handful of accounts?',
    formula: 'B = min( (unique_authors_14d / ln(mentions_14d)) × (1 − Gini) × 25,  25 )',
    normalization:
      'unique_authors / ln(mentions) scales for volume — 100 unique voices in 200 posts scores higher than 100 unique voices in 10,000 posts (the latter inflates mention count). (1 − Gini) further discounts when a few authors dominate.',
    why:
      'A coordinated campaign can spike mentions with a small number of accounts. Breadth of independent voices — adjusted for raw volume and post distribution — is the organic signal.',
  },
  {
    letter: 'C',
    name: 'Narrative strength',
    max: 25,
    what: 'Is there a coherent shared thesis? Requires the narrative detector to have run.',
    formula: 'C = (stage_map[stage] / 20) × stage_confidence × 25',
    normalization:
      'stage_map = {1:10, 2:18, 3:20, 4:10, 5:5, 6:2}. Stages 2–3 are the target window (peak = 25). stage_confidence ∈ [0, 1] from the cosine-graph cluster quality. Shows 0 until the hourly detector job runs.',
    why:
      'Not all mentions form a narrative. The detector clusters the last 72 h of embedded signals into coherent thesis groups and assigns a lifecycle stage. Stage 2–3 = forming + growing narrative — the ideal entry window before institutional consensus.',
  },
  {
    letter: 'D',
    name: 'Thesis quality',
    max: 20,
    what: 'What fraction of posts include real research vs. pure hype?',
    formula: 'D = ( min(s_br/0.75, 1)×0.5 + min(s_Br/0.25, 1)×0.5 ) × 20',
    normalization:
      's_br = share of classified posts where direction = bull AND substance = researched. s_Br = same for direction = bear AND substance = researched. Each is normalized against Reddit’s structural base rate (0.75 bull / 0.25 bear) so a rare bear DD scores proportionally the same as a common bull DD of equal relative prevalence.',
    why:
      'Researched posts (DD, earnings analysis, competitive moat) signal that the thesis has been stress-tested. A substantive bear case still counts — and is rewarded equally once normalized for base rate. Pure emotional momentum posts (YOLO, rocket emojis) can move price briefly but don’t sustain a multi-week narrative.',
  },
]

interface AdjustmentDef {
  condition: string
  multiplier: string
  why: string
}

const ADJUSTMENTS: AdjustmentDef[] = [
  {
    condition: 'Gini coefficient > 0.65',
    multiplier: '× 0.6',
    why: 'Top accounts responsible for most of the discussion — coordination or pump risk.',
  },
  {
    condition: '3 consecutive days of declining mentions',
    multiplier: '× 0.8',
    why: 'Narrative momentum is fading — the audience is losing interest.',
  },
  {
    condition: 'Lifecycle stage > 3',
    multiplier: '× 0.5',
    why: 'Narrative has already peaked. Stages 5–6 = crowded → fading. Too late for a clean entry.',
  },
  {
    condition: 'Market cap between $0 and $100M',
    multiplier: '× 0.85',
    why: 'Small-cap liquidity discount — higher manipulation risk, thinner options chains.',
  },
]

const SCORE_TIERS = [
  { range: '≥ 75', label: 'Strong signal', color: '#2ec27e', detail: 'All five components firing. Rare — take it at normal size.' },
  { range: '65–74', label: 'Good signal', color: '#1f9d55', detail: 'Solid narrative with at most one weak component.' },
  { range: '55–64', label: 'Developing', color: '#f59f00', detail: 'Stage 1 watch setups and early Stage 2 entries — narrative forming. Stage 2 at this range is worth monitoring seriously.' },
  { range: '45–54', label: 'Weak', color: '#e8590c', detail: 'Something structural is off — thin contributor base, low quality, or late stage.' },
  { range: '< 45', label: 'Pass', color: '#c92a2a', detail: 'Multiple red flags. Skip.' },
]

const FLAGS = [
  { flag: 'gini_high',        label: 'Concentrated posts',  desc: 'A small number of accounts drive most of the discussion. Check the source posts before acting.' },
  { flag: 'decelerating_3d',  label: 'Fading momentum',     desc: 'Mention rate has dropped 3 days in a row. The narrative may be cooling.' },
  { flag: 'late_stage',       label: 'Late stage',           desc: 'Narrative is past the ideal entry window (stage > 3).' },
  { flag: 'small_cap',        label: 'Small cap',            desc: 'Market cap under $100M. Extra caution on liquidity and manipulation risk.' },
  { flag: 'low_unique_authors', label: 'Few voices',         desc: 'Not enough distinct people posting yet for reliable signals.' },
  { flag: 'cold_start',       label: 'Early signal',         desc: 'Ticker has no lifecycle stage or classifier data yet. C and D scores are 0 — treat ACS as a rough attention proxy only.' },
]

export function ScoreLegend() {
  return (
    <details className="score-legend">
      <summary>How is this scored?</summary>
      <div className="score-legend-body">

        <h4>Score tiers (ACS out of 100)</h4>
        <p style={{ opacity: 0.8, fontSize: '0.9em', marginBottom: '0.5em' }}>
          The number measures <em>magnitude</em>. A small directional indicator below the score
          reflects the dominant conviction signal:{' '}
          <strong style={{ color: '#4ade80' }}>▲ bull</strong> when the dominant signal is bullish
          (analytical or hype-driven), <strong style={{ color: '#f87171' }}>▼ bear</strong> when
          bearish. No indicator when the signal is unknown or mixed.
        </p>
        <table className="legend-table">
          <thead>
            <tr><th>Score</th><th>Interpretation</th><th>Notes</th></tr>
          </thead>
          <tbody>
            {SCORE_TIERS.map(t => (
              <tr key={t.range}>
                <td><strong style={{ color: t.color }}>{t.range}</strong></td>
                <td><strong>{t.label}</strong></td>
                <td style={{ opacity: 0.8 }}>{t.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h4>Stage vs. Score — two independent dimensions</h4>
        <p style={{ opacity: 0.8, fontSize: '0.9em', marginBottom: '0.4em' }}>
          <strong>Stage</strong> describes the <em>shape</em> of the trajectory — is this narrative forming,
          growing, or peaking? It comes from the narrative detector and fires on structural rules
          (contributor growth, tier mix, discussion depth), not on volume.
        </p>
        <p style={{ opacity: 0.8, fontSize: '0.9em', marginBottom: '0.4em' }}>
          <strong>Score</strong> describes the <em>magnitude</em> of the signal — how credible and strong
          is it right now? It depends on absolute mention density (A), author breadth (B),
          stage confidence (C), and thesis quality (D).
        </p>
        <p style={{ opacity: 0.8, fontSize: '0.9em', marginBottom: '0.4em' }}>
          A ticker can be <strong>Stage 3 with a low score</strong>: the discussion is expanding
          (right shape) but still too thin to act on. As volume builds over the following weeks,
          Components A and C rise together and the score follows.
        </p>
        <p style={{ opacity: 0.8, fontSize: '0.9em', marginBottom: '1em' }}>
          A ticker can also have a <strong>high score but no stage badge</strong> (stage 0): the scorer
          ran when data was richer, but the detector's 72-hour clustering window currently has too few
          posts to form a cluster. The score reflects a prior run; treat it with caution until the
          badge reappears.
        </p>

        <h4>Component breakdown (total out of 100)</h4>
        {COMPONENTS.map(c => (
          <div key={c.letter} className="legend-component">
            <div className="legend-component-header">
              <span className="legend-component-letter">{c.letter}</span>
              <span className="legend-component-name">{c.name} <span className="legend-component-max">(max {c.max})</span></span>
            </div>
            <p className="legend-component-what">{c.what}</p>
            <dl className="legend-component-detail">
              <dt>Formula</dt><dd><code>{c.formula}</code></dd>
              <dt>Normalization</dt><dd style={{ whiteSpace: 'pre-line' }}>{c.normalization}</dd>
              <dt>Why it matters</dt><dd>{c.why}</dd>
            </dl>
          </div>
        ))}

        <h4>Score adjustments (multiplicative haircuts, applied in order)</h4>
        <p style={{ opacity: 0.7, fontSize: '0.85em', marginBottom: '0.5em' }}>
          Multipliers compound when multiple conditions fire. Worst case (all four): 0.6 × 0.8 × 0.5 × 0.85 = 0.204.
        </p>
        <table className="legend-table">
          <thead>
            <tr><th>Condition</th><th>Multiplier</th><th>Rationale</th></tr>
          </thead>
          <tbody>
            {ADJUSTMENTS.map(a => (
              <tr key={a.condition}>
                <td>{a.condition}</td>
                <td><strong>{a.multiplier}</strong></td>
                <td style={{ opacity: 0.8 }}>{a.why}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h4>Time decay</h4>
        <p style={{ opacity: 0.8, fontSize: '0.9em' }}>
          Scores decay as <code>ACS(t) = ACS₀ × e^{'{'}-0.07·t{'}'}</code> where <em>t</em> is days since the score was computed.
          Half-life ≈ 10 days. The <em>decay_acs</em> column reflects this; the raw <em>acs</em> score does not.
        </p>

        <h4>Warnings explained</h4>
        <ul>
          {FLAGS.map(f => (
            <li key={f.flag}>
              <strong>{f.label}:</strong> <span style={{ opacity: 0.8 }}>{f.desc}</span>
            </li>
          ))}
        </ul>

        <p style={{ opacity: 0.6, fontSize: '0.82em', marginTop: '1em' }}>
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

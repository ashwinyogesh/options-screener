/**
 * Lifecycle stage badge — methodology §4.
 *
 * Stages 1–6 map to a fixed colour ladder so the eye can scan a long table
 * for the target window (stages 2–3) without reading numbers.
 */

const STAGE_META: Record<number, { label: string; description: string; color: string }> = {
  0: { label: '…', description: 'Detecting — narrative detector is still analysing this ticker', color: '#888' },
  1: { label: '1', description: 'Early signal — scattered mentions, not yet a formed narrative', color: '#7a7a7a' },
  2: { label: '2', description: 'Forming ✓ — recurring discussion with analytical backing. Good entry window.', color: '#1f9d55' },
  3: { label: '3', description: 'Growing ✓ — more people joining the conversation each week. Good entry window.', color: '#2ec27e' },
  4: { label: '4', description: 'Maturing — institutional attention appearing. Late for new entries.', color: '#f59f00' },
  5: { label: '5', description: 'Crowded — mainstream coverage. Most upside already priced in.', color: '#e8590c' },
  6: { label: '6', description: 'Fading — momentum declining, exit signals appearing. Avoid.', color: '#c92a2a' },
}

interface StageBadgeProps {
  stage: number
  confidence?: number
}

export function StageBadge({ stage, confidence }: StageBadgeProps) {
  const meta = STAGE_META[stage] ?? STAGE_META[0]
  const conf = confidence == null ? '' : ` · conf ${(confidence * 100).toFixed(0)}%`
  return (
    <span
      className="stage-badge"
      style={{ backgroundColor: meta.color }}
      title={`Stage ${meta.label}: ${meta.description}${conf}`}
    >
      {meta.label}
    </span>
  )
}

/**
 * Plain-English labels for classifier conviction states.
 * Used in the table Signal column and the detail panel Conviction section.
 * Matches the 10 states defined in NARRATIVE_METHODOLOGY.md §3.
 */
export const SIGNAL_LABELS: Record<string, string> = {
  researched_bull:     'Analytical · Bullish',
  researched_bear:     'Analytical · Bearish',
  emotional_bull:      'Hype · Bullish',
  emotional_bear:      'Hype · Bearish',
  uncertainty:         'Undecided',
  earnings_focused:    'Earnings thesis',
  product_thesis:      'Product thesis',
  ecosystem_thesis:    'Sector thesis',
  institutional_watch: 'Institutional interest',
  exit_signal:         'Selling / exit',
}

/** Translate a raw conviction_state to a human-readable label. */
export function labelSignal(state: string | null | undefined): string {
  if (!state) return '—'
  return SIGNAL_LABELS[state] ?? state
}

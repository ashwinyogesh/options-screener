import type { SwingFilterState, SwingSetupType, SwingConfidence, SwingScorerVersion } from '../types/swing'

interface Props {
  filters: SwingFilterState
  onChange: (f: SwingFilterState) => void
  scorerVersion?: SwingScorerVersion
}

const SETUP_OPTIONS: { value: SwingSetupType | 'all'; label: string }[] = [
  { value: 'all', label: 'All setups' },
  { value: 'breakout', label: 'Breakout' },
  { value: 'momentum', label: 'Momentum' },
  { value: 'reversion', label: 'Reversion' },
  { value: 'retest', label: 'Retest' },
]

const CONFIDENCE_OPTIONS: { value: SwingConfidence | 'all'; label: string }[] = [
  { value: 'all', label: 'Any confidence' },
  { value: 'high', label: 'High only' },
  { value: 'medium', label: 'Medium+' },
  { value: 'speculative', label: 'Speculative+' },
]

export function SwingFilterPanel({ filters, onChange, scorerVersion = 'v3' }: Props) {
  function set<K extends keyof SwingFilterState>(key: K, value: SwingFilterState[K]) {
    onChange({ ...filters, [key]: value })
  }

  return (
    <div className="filter-panel">
      <span className="filter-label">Filters:</span>

      <label className="filter-item">
        Setup:
        <select
          value={filters.setupType}
          onChange={e => set('setupType', e.target.value as SwingSetupType | 'all')}
          className="filter-select"
        >
          {SETUP_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>

      <label className="filter-item">
        Min R:R ≥
        <input
          type="number"
          className="filter-number"
          value={filters.minRR}
          min={0}
          max={10}
          step={0.25}
          onChange={e => set('minRR', Number(e.target.value))}
        />
        <span className="filter-hint">(0 = off)</span>
      </label>

      <label className="filter-item">
        {scorerVersion === 'v3' ? 'Min chance ≥' : 'Min score ≥'}
        <input
          type="number"
          className="filter-number"
          value={filters.minScore}
          min={0}
          max={100}
          step={5}
          onChange={e => set('minScore', Number(e.target.value))}
        />
        <span className="filter-hint">
          {scorerVersion === 'v3' ? '(P(target) %, 0 = off)' : '(0 = off)'}
        </span>
      </label>

      <label className="filter-item">
        Confidence:
        <select
          value={filters.minConfidence}
          onChange={e => set('minConfidence', e.target.value as SwingConfidence | 'all')}
          className="filter-select"
        >
          {CONFIDENCE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>

      <label className="filter-item filter-toggle">
        <input
          type="checkbox"
          checked={filters.excludeEarningsWarning}
          onChange={e => set('excludeEarningsWarning', e.target.checked)}
        />
        Exclude earnings ≤ 10d
      </label>
    </div>
  )
}

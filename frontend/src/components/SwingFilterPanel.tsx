import type { SwingFilterState, SwingSetupType, SwingConfidence } from '../types/swing'

interface Props {
  filters: SwingFilterState
  onChange: (f: SwingFilterState) => void
}

const SETUP_OPTIONS: { value: SwingSetupType | 'all'; label: string }[] = [
  { value: 'all', label: 'All patterns' },
  { value: 'breakout', label: 'Breakout' },
  { value: 'momentum', label: 'Momentum' },
  { value: 'reversion', label: 'Bounce' },
  { value: 'retest', label: 'Retest' },
]

const CONFIDENCE_OPTIONS: { value: SwingConfidence | 'all'; label: string }[] = [
  { value: 'all', label: 'Any quality' },
  { value: 'high', label: 'Strong only' },
  { value: 'medium', label: 'Medium+' },
  { value: 'speculative', label: 'All' },
]

export function SwingFilterPanel({ filters, onChange }: Props) {
  function set<K extends keyof SwingFilterState>(key: K, value: SwingFilterState[K]) {
    onChange({ ...filters, [key]: value })
  }

  return (
    <div className="filter-panel">
      <span className="filter-label">Filters:</span>

      <label className="filter-item">
        Pattern:
        <select
          value={filters.setupType}
          onChange={e => set('setupType', e.target.value as SwingSetupType | 'all')}
          className="filter-select"
        >
          {SETUP_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>

      <label className="filter-item">
        Min Reward/Risk ≥
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
        Min Score ≥
        <input
          type="number"
          className="filter-number"
          value={filters.minScore}
          min={0}
          max={100}
          step={5}
          onChange={e => set('minScore', Number(e.target.value))}
        />
        <span className="filter-hint">(0 = off, 65 recommended)</span>
      </label>

      <label className="filter-item">
        Quality:
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
        Hide earnings risk
      </label>
    </div>
  )
}

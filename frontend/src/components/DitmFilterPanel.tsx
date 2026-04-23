import type { DitmFilterState } from '../types/ditm'

interface Props {
  filters: DitmFilterState
  onChange: (f: DitmFilterState) => void
}

export function DitmFilterPanel({ filters, onChange }: Props) {
  function set<K extends keyof DitmFilterState>(key: K, value: DitmFilterState[K]) {
    onChange({ ...filters, [key]: value })
  }

  return (
    <div className="filter-panel">
      <span className="filter-label">Filters:</span>

      <label className="filter-item">
        Min Delta ≥
        <input type="number" className="filter-number" value={filters.minDelta}
          min={0} max={1} step={0.05} onChange={e => set('minDelta', Number(e.target.value))} />
        <span className="filter-hint">(0.65–0.95 typical)</span>
      </label>

      <label className="filter-item">
        Extrinsic% ≤
        <input type="number" className="filter-number" value={filters.maxExtrinsicPct}
          min={0} max={20} step={0.5} onChange={e => set('maxExtrinsicPct', Number(e.target.value))} />
        <span className="filter-hint">(0 = off; % of stock price)</span>
      </label>

      <label className="filter-item">
        Spread% ≤
        <input type="number" className="filter-number" value={filters.maxSpreadPct}
          min={0} max={100} step={1} onChange={e => set('maxSpreadPct', Number(e.target.value))} />
        <span className="filter-hint">(0 = off)</span>
      </label>

      <label className="filter-item filter-toggle">
        <input type="checkbox" checked={filters.smaRatioBullishOnly}
          onChange={e => set('smaRatioBullishOnly', e.target.checked)} />
        SMA50 &gt; SMA200
      </label>

      <label className="filter-item filter-toggle">
        <input type="checkbox" checked={filters.excludeEarningsWithinDte}
          onChange={e => set('excludeEarningsWithinDte', e.target.checked)} />
        Exclude earnings in DTE
      </label>
    </div>
  )
}

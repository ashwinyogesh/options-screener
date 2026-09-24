import type { CspResult } from '../types/csp'
import type { AllocatorSettings, AllocationPick } from '../types/cspAllocator'
import { useCspAllocator } from '../hooks/useCspAllocator'
import { happyBadge } from '../utils/happyPrice'

interface Props {
  results: CspResult[]
}

function usd(n: number): string {
  return '$' + Math.round(n).toLocaleString()
}
function pct(n: number, digits = 0): string {
  return n.toFixed(digits) + '%'
}

const HAPPY_STYLE: Record<string, { label: string; className: string }> = {
  below: { label: '🟢', className: 'happy-below' },
  inside: { label: '🟡', className: 'happy-inside' },
  above: { label: '🔴', className: 'happy-above' },
}

/** Happy-price cell: point + break-even badge, with the zone/spread in the tooltip. */
function HappyCell({ p }: { p: AllocationPick }) {
  const hp = p.happy
  if (hp.downtrend) return <span className="happy-dt" title="Downtrend — support is a falling knife, no happy price">⚠ dt</span>
  if (hp.point == null || hp.zoneLo == null || hp.zoneHi == null) return <span className="dim">—</span>
  const breakEven = p.strike - p.premiumPerContract / 100
  const badge = happyBadge(hp, breakEven)
  const style = HAPPY_STYLE[badge]
  const families = hp.anchors
    .map((a) => `  ${a.inConsensus ? '✓' : '·'} ${a.label} $${a.price.toFixed(0)}`)
    .join('\n')
  const title =
    `Happy zone $${hp.zoneLo.toFixed(0)}–$${hp.zoneHi.toFixed(0)} · width ${hp.spreadPct?.toFixed(0)}% · ` +
    `${hp.familyCount} families agree (${hp.confidence})\n` +
    `${families}\n` +
    `Break-even $${breakEven.toFixed(2)} is ${badge} the support zone`
  return (
    <span className={style?.className} title={title}>
      {style?.label} ${hp.point.toFixed(0)}
      <span className="happy-spread"> ±{hp.spreadPct?.toFixed(0)}%</span>
    </span>
  )
}

const RISK_LABEL = ['Conservative', 'Cautious', 'Balanced', 'Growth', 'Aggressive']

export function CspAllocatorPanel({ results }: Props) {
  const { capital, setCapital, settings, setSettings, result } = useCspAllocator(results)

  function set<K extends keyof AllocatorSettings>(key: K, value: AllocatorSettings[K]) {
    setSettings({ ...settings, [key]: value })
  }

  const riskIdx = Math.round(settings.riskTolerance * (RISK_LABEL.length - 1))
  const optMode = settings.mode === 'optimized'

  return (
    <details className="allocator">
      <summary className="allocator-summary">
        💰 Capital Allocator — build the best basket for your budget
      </summary>

      <div className="allocator-body">
        <div className="allocator-modes">
          <button
            type="button"
            className={`allocator-mode-btn${!optMode ? ' allocator-mode-active' : ''}`}
            onClick={() => set('mode', 'edge')}
          >
            Best edge
          </button>
          <button
            type="button"
            className={`allocator-mode-btn${optMode ? ' allocator-mode-active' : ''}`}
            onClick={() => set('mode', 'optimized')}
          >
            Optimized
          </button>
        </div>

        <p className="allocator-intro">
          {optMode ? (
            <>
              Runs a <strong>bounded-knapsack optimizer</strong> over every strike on screen and jointly
              picks the strike per name that best fills your capital — here <strong>safety = assignment
              probability</strong> (|put delta|) and <strong>return = vol-normalised yield</strong> (ROC per
              1% of the stock's expected move, so high IV isn't mistaken for free edge). It weights merit
              by <strong>dollars deployed</strong> (not contract count) and caps each name so the book stays
              spread. The slider trades delta-safety against yield efficiency.
            </>
          ) : (
            <>
              Splits your capital across a basket of the CSP contracts currently on screen, ranking by
              risk-adjusted edge — instead of sinking it into one large position. Contracts scoring below
              the gate are never used just to deploy cash.
            </>
          )}
        </p>

        <div className="allocator-controls">
          <label className="filter-item">
            Capital&nbsp;$
            <input
              type="number"
              className="filter-number filter-number-wide"
              value={capital || ''}
              min={0}
              step={1000}
              placeholder="15000"
              onChange={e => setCapital(Number(e.target.value))}
            />
          </label>

          <label className="filter-item allocator-slider">
            Style
            <input
              type="range"
              min={0}
              max={1}
              step={0.25}
              value={settings.riskTolerance}
              onChange={e => set('riskTolerance', Number(e.target.value))}
            />
            <span className="allocator-slider-label">{RISK_LABEL[riskIdx]}</span>
            {optMode && <span className="filter-hint">(safety = |Δ|)</span>}
          </label>
        </div>

        <details className="allocator-advanced">
          <summary className="allocator-advanced-summary">Advanced</summary>
          <div className="allocator-controls">
            <label className="filter-item">
              Min score
              <input
                type="number"
                className="filter-number"
                value={settings.minScore}
                min={0}
                max={100}
                step={1}
                onChange={e => set('minScore', Number(e.target.value))}
              />
            </label>

            {optMode && (
              <label className="filter-item">
                Max/name %
                <input
                  type="number"
                  className="filter-number"
                  value={Math.round(settings.maxNamePct * 100)}
                  min={5}
                  max={100}
                  step={5}
                  onChange={e => set('maxNamePct', Number(e.target.value) / 100)}
                />
              </label>
            )}

            {optMode && (
              <label className="filter-item">
                Diversify %
                <input
                  type="number"
                  className="filter-number"
                  value={Math.round(settings.diversification * 100)}
                  min={0}
                  max={100}
                  step={10}
                  onChange={e => set('diversification', Number(e.target.value) / 100)}
                />
                <span className="filter-hint">(0 = off)</span>
              </label>
            )}
          </div>
        </details>

        {result == null && (
          <p className="allocator-hint">
            Enter your capital above to build a basket from the {results.length} name
            {results.length === 1 ? '' : 's'} on screen.
          </p>
        )}

        {result != null && result.picks.length > 0 && (
          <>
            <div className="allocator-summary-grid">
              <Stat label="Deployed" value={`${usd(result.totalDeployed)}`} sub={`${pct(result.utilizationPct)} of capital`} />
              <Stat label="Idle cash" value={usd(result.idleCash)} sub={result.idleCash > 0 ? 'left uncommitted' : 'fully deployed'} />
              <Stat label="Basket" value={`${result.numNames} names`} sub={`${result.numContracts} contracts`} />
              <Stat label="Premium credit" value={usd(result.totalCredit)} sub="collected up front" />
              <Stat label="Avg score" value={result.weightedScore.toFixed(0)} sub="capital-weighted" />
              <Stat label="ROC / deployed" value={pct(result.weightedRocOnDeployed, 1)} sub="annualized" />
              <Stat label="ROC / capital" value={pct(result.rocOnCapital, 1)} sub="incl. idle drag" />
              {optMode && (
                <Stat label="Avg Δ" value={result.weightedDelta.toFixed(2)} sub="assignment prob (safety)" />
              )}
              {optMode && (
                <Stat label="Yield eff" value={result.weightedYieldEff.toFixed(2)} sub="per-trade ROC / 1% move" />
              )}
              <Stat label="Top name" value={pct(result.largestNamePct * 100)} sub="of capital" />
            </div>

            <table className="allocator-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th className="num">Price</th>
                  <th className="num">Vol sup</th>
                  <th className="num">EM ↓</th>
                  <th className="num">Happy</th>
                  <th className="num">Strike</th>
                  <th className="num">Contracts</th>
                  <th className="num">Collateral</th>
                  <th className="num">% Cap</th>
                  <th className="num">Credit</th>
                  <th className="num">Score</th>
                  <th className="num">ROC</th>
                  {optMode && <th className="num">Δ</th>}
                  {optMode && <th className="num">Yld eff</th>}
                  <th>Expiry</th>
                </tr>
              </thead>
              <tbody>
                {result.picks.map(p => (
                  <tr key={`${p.symbol}-${p.strike}-${p.expiration}`}>
                    <td className="allocator-sym">{p.symbol}</td>
                    <td className="num">${p.price.toFixed(2)}</td>
                    <td className="num allocator-stack">
                      {[p.volSupport1, p.volSupport2, p.volSupport3].some(v => v != null)
                        ? [p.volSupport1, p.volSupport2, p.volSupport3].map((v, i) => (
                            v != null ? <div key={i}>{v.toFixed(2)}</div> : null
                          ))
                        : '—'}
                    </td>
                    <td className="num">${p.expMoveLower.toFixed(2)}</td>
                    <td className="num"><HappyCell p={p} /></td>
                    <td className="num">${p.strike.toFixed(0)}</td>
                    <td className="num allocator-strong">{p.contracts}×</td>
                    <td className="num">{usd(p.deployed)}</td>
                    <td className="num">{pct(p.capitalPct * 100)}</td>
                    <td className="num allocator-credit">{usd(p.credit)}</td>
                    <td className="num">{p.cspScore.toFixed(0)}</td>
                    <td className="num">{pct(p.roc, 1)}</td>
                    {optMode && <td className="num">{p.absDelta.toFixed(2)}</td>}
                    {optMode && <td className="num">{p.yieldEff.toFixed(2)}</td>}
                    <td className="allocator-exp">{p.expiration} · {p.dte}d</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {result.sectorBreakdown.length > 0 && (
              <div className="allocator-sectors">
                <span className="allocator-sectors-label">Sector mix:</span>
                {result.sectorBreakdown.map(s => (
                  <span key={s.sector} className="allocator-sector-chip">
                    {s.sector} {pct(s.pct * 100)}
                  </span>
                ))}
              </div>
            )}

            {result.baseline != null && (
              <div className="allocator-compare">
                <strong>Diversified basket vs one concentrated position</strong>
                <p>
                  Putting the whole {usd(result.capital)} into your top-ranked name
                  (<b>{result.baseline.symbol}</b>, {result.baseline.contracts}× at ${result.baseline.strike.toFixed(0)})
                  collects {usd(result.baseline.credit)} in premium but leaves <b>100% of the book on one stock</b>.
                  The basket collects {usd(result.totalCredit)} across <b>{result.numNames} names</b>, with the
                  largest at <b>{pct(result.largestNamePct * 100)}</b> of capital — near-equal income, a fraction of the
                  single-name tail risk. That variance reduction is the real edge; it only holds when the names
                  aren't all the same sector, so watch the sector mix above.
                </p>
              </div>
            )}

            {result.notes.length > 0 && (
              <ul className="allocator-notes">
                {result.notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            )}
          </>
        )}

        {result != null && result.picks.length === 0 && result.notes.length > 0 && (
          <ul className="allocator-notes">
            {result.notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        )}
      </div>
    </details>
  )
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="allocator-stat">
      <div className="allocator-stat-label">{label}</div>
      <div className="allocator-stat-value">{value}</div>
      <div className="allocator-stat-sub">{sub}</div>
    </div>
  )
}

import type { CcBacktestResult } from '../types/ccBacktest'

interface Props {
  data: CcBacktestResult
}

function fmtPct(n: number): string {
  return (n >= 0 ? '+' : '') + n.toFixed(1) + '%'
}

function rocColor(n: number): string {
  if (n >= 10) return '#4ade80'
  if (n >= 0) return '#a3e635'
  if (n >= -10) return '#fb923c'
  return '#f87171'
}

function EquityCurve({ curve }: { curve: number[] }) {
  if (curve.length < 2) return null
  const W = 640
  const H = 120
  const PAD = 4
  const min = Math.min(0, ...curve)
  const max = Math.max(0, ...curve)
  const range = max - min || 1
  const xStep = (W - PAD * 2) / (curve.length - 1)
  const yScale = (v: number) => H - PAD - ((v - min) / range) * (H - PAD * 2)
  const points = curve.map((v, i) => `${PAD + i * xStep},${yScale(v)}`).join(' ')
  const zeroY = yScale(0)
  const last = curve[curve.length - 1]
  const stroke = last >= 0 ? '#4ade80' : '#f87171'
  const fill = last >= 0 ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)'
  const areaPoints = `${PAD},${zeroY} ${points} ${PAD + (curve.length - 1) * xStep},${zeroY}`

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: 'block' }}>
      <polygon points={areaPoints} fill={fill} />
      <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="#475569" strokeWidth={0.5} strokeDasharray="3,3" />
      <polyline points={points} fill="none" stroke={stroke} strokeWidth={1.5} />
    </svg>
  )
}

export function CcBacktestPanel({ data }: Props) {
  const { summary, buckets, trades, caveats, symbol, years, dte, scan_start, scan_end } = data
  const totalPnl = summary.equity_curve.length ? summary.equity_curve[summary.equity_curve.length - 1] : 0

  let rhoLabel = '—'
  if (summary.n_trades >= 5) {
    const sig = summary.spearman_p < 0.05 ? ' ✓' : ''
    rhoLabel = `ρ = ${summary.spearman_rho.toFixed(3)}${sig}`
  }

  return (
    <div className="cc-backtest-panel" style={{
      padding: '14px 18px',
      background: '#0f172a',
      borderTop: '2px solid #334155',
      color: '#e2e8f0',
      fontSize: '13px',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
        <div>
          <strong style={{ fontSize: '14px' }}>📊 {symbol} CC Backtest</strong>
          <span style={{ marginLeft: 10, opacity: 0.7, fontSize: '11px' }}>
            {scan_start} → {scan_end} · weekly · {dte} DTE · {years}y · {summary.n_trades} trades
          </span>
        </div>
        <div style={{ fontSize: '11px', opacity: 0.6 }}>
          Methodology: HV(30) IV proxy · BA/LQ omitted (renormalised) · total CC P&L
        </div>
      </div>

      {/* Headline tiles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 10, marginBottom: 14 }}>
        <Tile label="Mean ROC (ann)" value={fmtPct(summary.mean_roc)} color={rocColor(summary.mean_roc)} />
        <Tile label="Win rate" value={summary.win_rate.toFixed(1) + '%'} color={summary.win_rate >= 75 ? '#4ade80' : '#fbbf24'} />
        <Tile label="Called away" value={summary.n_assigned + ' / ' + summary.n_trades} sub={summary.assign_rate.toFixed(1) + '%'} />
        <Tile label="Mean score" value={summary.mean_score.toFixed(1)} />
        <Tile label="Spearman" value={rhoLabel} sub={summary.monotone_buckets ? 'monotone ✓' : 'non-monotone'} color={summary.spearman_rho > 0 ? '#4ade80' : '#f87171'} />
        <Tile label="Total P&L" value={'$' + Math.round(totalPnl).toLocaleString()} color={totalPnl >= 0 ? '#4ade80' : '#f87171'} sub="1 contract" />
      </div>

      {/* Equity curve */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: '11px', opacity: 0.7, marginBottom: 4 }}>
          Cumulative P&L (per contract, $ — stock + short call, marked to expiry)
        </div>
        <div style={{ background: '#020617', borderRadius: 4, padding: 4 }}>
          <EquityCurve curve={summary.equity_curve} />
        </div>
      </div>

      {/* Bucket table */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: '11px', opacity: 0.7, marginBottom: 4 }}>
          Per-score-bucket performance — does the score predict the outcome?
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', tableLayout: 'fixed' }}>
          <colgroup>
            <col style={{ width: '28%' }} />
            <col style={{ width: '10%' }} />
            <col style={{ width: '15%' }} />
            <col style={{ width: '15%' }} />
            <col style={{ width: '16%' }} />
            <col style={{ width: '16%' }} />
          </colgroup>
          <thead>
            <tr style={{ borderBottom: '1px solid #334155', opacity: 0.7 }}>
              <th style={{ textAlign: 'left', padding: '4px 8px' }}>Score range</th>
              <th style={{ textAlign: 'right', padding: '4px 8px' }}>n</th>
              <th style={{ textAlign: 'right', padding: '4px 8px' }}>Mean ROC</th>
              <th style={{ textAlign: 'right', padding: '4px 8px' }}>Median ROC</th>
              <th style={{ textAlign: 'right', padding: '4px 8px' }}>Win rate</th>
              <th style={{ textAlign: 'right', padding: '4px 8px' }}>Called away</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map(b => (
              <tr key={b.bucket} style={{ borderBottom: '1px solid #1e293b', opacity: b.n === 0 ? 0.4 : 1 }}>
                <td style={{ textAlign: 'left', padding: '4px 8px' }}>{b.bucket}{b.bucket === '65-75' && ' (tradeable)'}</td>
                <td style={{ textAlign: 'right', padding: '4px 8px', fontVariantNumeric: 'tabular-nums' }}>{b.n}</td>
                <td style={{ textAlign: 'right', padding: '4px 8px', fontVariantNumeric: 'tabular-nums', color: b.n ? rocColor(b.mean_roc) : undefined }}>{b.n ? fmtPct(b.mean_roc) : '—'}</td>
                <td style={{ textAlign: 'right', padding: '4px 8px', fontVariantNumeric: 'tabular-nums' }}>{b.n ? fmtPct(b.median_roc) : '—'}</td>
                <td style={{ textAlign: 'right', padding: '4px 8px', fontVariantNumeric: 'tabular-nums' }}>{b.n ? b.win_rate.toFixed(0) + '%' : '—'}</td>
                <td style={{ textAlign: 'right', padding: '4px 8px', fontVariantNumeric: 'tabular-nums' }}>{b.n ? b.assign_rate.toFixed(0) + '%' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {summary.cutoff_delta_roc !== 0 && (
          <div style={{ fontSize: '11px', marginTop: 6, opacity: 0.85 }}>
            <strong>65-cutoff Δ:</strong>{' '}
            <span style={{ color: summary.cutoff_delta_roc > 0 ? '#4ade80' : '#f87171' }}>
              {fmtPct(summary.cutoff_delta_roc)}
            </span>{' '}
            mean-ROC separation between tradeable (≥65) and skip (&lt;65).
          </div>
        )}
      </div>

      {/* Trade ledger */}
      <details>
        <summary style={{ cursor: 'pointer', fontSize: '11px', opacity: 0.8, marginBottom: 6 }}>
          ▸ Show per-trade ledger ({trades.length} rows)
        </summary>
        <div style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid #1e293b', borderRadius: 4 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px' }}>
            <thead style={{ position: 'sticky', top: 0, background: '#1e293b' }}>
              <tr style={{ textAlign: 'right', opacity: 0.8 }}>
                <th style={{ textAlign: 'left', padding: '4px 6px' }}>Scan date</th>
                <th>Spot</th>
                <th>Strike</th>
                <th>Δ</th>
                <th>Premium</th>
                <th>Score</th>
                <th>Spot @ exp</th>
                <th>Called?</th>
                <th>P&L</th>
                <th>ROC ann</th>
              </tr>
            </thead>
            <tbody>
              {trades.map(t => (
                <tr key={t.scan_date} style={{ borderBottom: '1px solid #0f172a', textAlign: 'right' }}>
                  <td style={{ textAlign: 'left', padding: '3px 6px' }}>{t.scan_date}</td>
                  <td>${t.spot.toFixed(2)}</td>
                  <td>${t.strike.toFixed(2)}</td>
                  <td>+{t.delta.toFixed(3)}</td>
                  <td>${t.premium.toFixed(2)}</td>
                  <td>{t.final_score.toFixed(1)}</td>
                  <td>${t.spot_at_exp.toFixed(2)}</td>
                  <td style={{ color: t.assigned ? '#fb923c' : '#94a3b8' }}>{t.assigned ? 'yes' : '—'}</td>
                  <td style={{ color: t.pnl_per_contract >= 0 ? '#4ade80' : '#f87171' }}>
                    ${t.pnl_per_contract.toFixed(0)}
                  </td>
                  <td style={{ color: rocColor(t.realised_roc_annualised) }}>
                    {fmtPct(t.realised_roc_annualised)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      {/* Caveats */}
      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: 'pointer', fontSize: '11px', opacity: 0.7 }}>
          ⓘ Methodology &amp; limitations ({caveats.length})
        </summary>
        <ul style={{ fontSize: '11px', opacity: 0.75, paddingLeft: 18, marginTop: 4 }}>
          {caveats.map((c, i) => <li key={i} style={{ marginBottom: 3 }}>{c}</li>)}
        </ul>
      </details>
    </div>
  )
}

function Tile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{
      background: '#1e293b',
      padding: '8px 10px',
      borderRadius: 4,
      borderLeft: color ? `3px solid ${color}` : '3px solid #475569',
    }}>
      <div style={{ fontSize: '10px', opacity: 0.6, marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: '15px', fontWeight: 600, color: color ?? '#e2e8f0' }}>{value}</div>
      {sub && <div style={{ fontSize: '10px', opacity: 0.6 }}>{sub}</div>}
    </div>
  )
}

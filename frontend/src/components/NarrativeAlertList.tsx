import type { NarrativeAlert } from '../types/narrative'

interface NarrativeAlertListProps {
  alerts: NarrativeAlert[]
}

const ALERT_LABELS: Record<string, string> = {
  acs_rising_fast: 'ACS spike',
  stage_2_entry:   'Stage 2 entry',
  stage_3_entry:   'Stage 3 entry',
}

function alertDetail(alert: NarrativeAlert): string | null {
  const p = alert.payload
  if (alert.alert_type === 'acs_rising_fast' && p.delta != null) {
    return `+${Number(p.delta).toFixed(1)} pts`
  }
  if ((alert.alert_type === 'stage_2_entry' || alert.alert_type === 'stage_3_entry') && p.acs != null) {
    return `ACS ${Number(p.acs).toFixed(1)}`
  }
  return null
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function NarrativeAlertList({ alerts }: NarrativeAlertListProps) {
  if (alerts.length === 0) {
    return <p className="muted">No active alerts.</p>
  }
  return (
    <ul className="narrative-alert-list">
      {alerts.map((alert, i) => {
        const label = ALERT_LABELS[alert.alert_type] ?? alert.alert_type
        const detail = alertDetail(alert)
        return (
          <li key={`${alert.ticker}-${alert.triggered_at}-${i}`}>
            <div className="alert-row">
              <span className="alert-ticker">{alert.ticker}</span>
              <span className={`alert-type-badge alert-type--${alert.alert_type}`}>{label}</span>
              {detail && <span className="alert-detail">{detail}</span>}
            </div>
            <div className="alert-time muted">{formatTime(alert.triggered_at)}</div>
          </li>
        )
      })}
    </ul>
  )
}

import type { NarrativeAlert } from '../types/narrative'

interface NarrativeAlertListProps {
  alerts: NarrativeAlert[]
}

export function NarrativeAlertList({ alerts }: NarrativeAlertListProps) {
  if (alerts.length === 0) {
    return <p className="muted">No active alerts.</p>
  }
  return (
    <ul className="narrative-alert-list">
      {alerts.map((alert, i) => (
        <li key={`${alert.ticker}-${alert.triggered_at}-${i}`}>
          <span className="alert-ticker">{alert.ticker}</span>
          <span className="alert-type">{alert.alert_type}</span>
          <span className="alert-time muted">
            {new Date(alert.triggered_at).toLocaleString()}
          </span>
        </li>
      ))}
    </ul>
  )
}

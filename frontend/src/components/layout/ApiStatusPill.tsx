import { useSystemStatus, type Connectivity } from '../../features/system/useSystemStatus.ts';

const LABELS: Record<Connectivity, { tone: string; short: string; long: string }> = {
  checking: { tone: 'unknown', short: 'Checking', long: 'Checking API…' },
  online: { tone: 'healthy', short: 'Online', long: 'API online' },
  degraded: { tone: 'degraded', short: 'Degraded', long: 'API up · database unavailable' },
  offline: { tone: 'down', short: 'Offline', long: 'API unreachable' },
};

/** Live backend connectivity: polls /health and /ready. Text + dot, never colour alone. */
export function ApiStatusPill() {
  const { connectivity, data } = useSystemStatus();
  const { tone, short, long } = LABELS[connectivity];
  const checked = data ? new Date(data.checkedAt).toLocaleTimeString() : 'not yet';
  return (
    <span
      className={`api-pill tone-${tone}`}
      role="status"
      aria-label={`${long}. Last checked ${checked}.`}
      title={`${long} · last checked ${checked}`}
      data-connectivity={connectivity}
    >
      <span className="status-dot" aria-hidden="true" />
      <span aria-hidden="true">
        <span className="api-pill__text-long">API </span>
        {short}
      </span>
    </span>
  );
}

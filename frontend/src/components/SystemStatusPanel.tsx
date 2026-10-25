import { config } from '../config.ts';
import { useSystemStatus } from '../hooks/useSystemStatus.ts';
import type { ProbeResult } from '../lib/api.ts';

const LABELS: Record<ProbeResult['state'], string> = {
  operational: 'Operational',
  unavailable: 'Unavailable',
  unreachable: 'Unreachable',
};

function StatusRow({
  name,
  detail,
  result,
}: {
  name: string;
  detail: string;
  result: ProbeResult | null;
}) {
  const state = result?.state ?? 'pending';
  const label = result ? LABELS[result.state] : 'Checking…';
  return (
    <li className="status-row">
      <div>
        <p className="status-row__name">{name}</p>
        <p className="status-row__detail">{detail}</p>
      </div>
      <span className={`badge badge--${state}`} data-testid={`status-${name.toLowerCase()}`}>
        <span className="badge__dot" aria-hidden="true" />
        {label}
      </span>
    </li>
  );
}

export function SystemStatusPanel() {
  const { api, database, checkedAt, loading, refresh } = useSystemStatus();

  return (
    <section className="card" aria-labelledby="system-status-heading">
      <div className="card__header">
        <div>
          <h2 id="system-status-heading" className="card__title">
            System status
          </h2>
          <p className="card__subtitle">
            Platform self-check against <code>{config.apiUrl}</code>
          </p>
        </div>
        <button type="button" className="button" onClick={refresh} disabled={loading}>
          {loading ? 'Checking…' : 'Refresh'}
        </button>
      </div>
      <ul className="status-list">
        <StatusRow name="API" detail="Liveness · GET /health" result={api} />
        <StatusRow name="Database" detail="Readiness · GET /ready" result={database} />
      </ul>
      <p className="card__footnote" aria-live="polite">
        {checkedAt ? `Last checked ${checkedAt.toLocaleTimeString()}` : 'Running first check…'}
      </p>
    </section>
  );
}

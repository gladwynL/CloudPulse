import { useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  CircleCheck,
  CircleHelp,
  CircleX,
  Flame,
  Plus,
  RefreshCw,
  Server,
  ShieldAlert,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { StatusBadge } from '../../components/ui/Badges.tsx';
import { Button } from '../../components/ui/Button.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, ErrorState, LoadingState, Refreshing } from '../../components/ui/States.tsx';
import { RelativeTime } from '../../components/ui/Time.tsx';
import type { Overview, Resource, ResourceStatus } from '../../lib/api/types.ts';
import { keys } from '../../lib/queryKeys.ts';
import { STATUS_ORDER } from '../../lib/utils/status.ts';
import { displayTarget, formatCount, formatMs, formatPercent } from '../../lib/utils/format.ts';
import { useSystemStatus } from '../system/useSystemStatus.ts';
import { IncidentCompactList } from '../incidents/IncidentList.tsx';
import {
  SERVICE_LIST_LIMIT,
  useActiveIncidents,
  useOverview,
  useRecentlyResolved,
  useServiceList,
} from './hooks.ts';

function Stat({
  label,
  value,
  icon: Icon,
  tone,
  to,
  hint,
}: {
  label: string;
  value: number;
  icon: LucideIcon;
  tone: string;
  to?: string;
  hint?: string;
}) {
  const body = (
    <>
      <span className="stat__label">
        <Icon size={15} aria-hidden="true" />
        {label}
      </span>
      <span className="stat__value">{formatCount(value)}</span>
      {hint && <span className="stat__hint">{hint}</span>}
    </>
  );
  const style = { ['--tone' as string]: `var(--${tone})` };
  return to ? (
    <Link to={to} className="stat" style={style}>
      {body}
    </Link>
  ) : (
    <div className="stat" style={style}>
      {body}
    </div>
  );
}

const SEGMENTS: { status: ResourceStatus; label: string; tone: string }[] = [
  { status: 'HEALTHY', label: 'Healthy', tone: 'healthy' },
  { status: 'DEGRADED', label: 'Degraded', tone: 'degraded' },
  { status: 'DOWN', label: 'Down', tone: 'down' },
  { status: 'UNKNOWN', label: 'Unknown', tone: 'unknown' },
];

export function HealthDistribution({
  counts,
  total,
}: {
  counts: Overview['resources']['by_status'];
  total: number;
}) {
  const summary = SEGMENTS.map((s) => `${counts[s.status]} ${s.label.toLowerCase()}`).join(', ');
  return (
    <div className="stack">
      <div
        className="dist"
        role="img"
        aria-label={`Health distribution of ${total} resources: ${summary}`}
      >
        {SEGMENTS.filter((s) => counts[s.status] > 0).map((s) => (
          <span
            key={s.status}
            className="dist__seg"
            style={{
              width: `${(counts[s.status] / total) * 100}%`,
              ['--tone' as string]: `var(--${s.tone})`,
            }}
          />
        ))}
      </div>
      <ul className="legend">
        {SEGMENTS.map((s) => (
          <li
            key={s.status}
            className="legend__item"
            style={{ ['--tone' as string]: `var(--${s.tone})` }}
          >
            <span className="legend__swatch" aria-hidden="true" />
            {s.label} <strong style={{ color: 'var(--text)' }}>{counts[s.status]}</strong>
            <span className="subtle">({formatPercent((counts[s.status] / total) * 100)})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ServiceList({ resources, total }: { resources: Resource[]; total: number }) {
  const sorted = [...resources].sort(
    (a, b) =>
      STATUS_ORDER[a.current_status] - STATUS_ORDER[b.current_status] ||
      a.name.localeCompare(b.name),
  );
  return (
    <div className="stack">
      <div
        className="table-wrap"
        role="region"
        aria-label="Monitored services, most severe first"
        tabIndex={0}
      >
        <table className="table">
          <caption className="sr-only">Monitored services, most severe first</caption>
          <thead>
            <tr>
              <th scope="col">Service</th>
              <th scope="col">Status</th>
              <th scope="col" className="num">
                Latency
              </th>
              <th scope="col">Last checked</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.id}>
                <td>
                  <div className="cell-title">
                    <Link to={`/resources/${r.id}`}>{r.name}</Link>
                    <span className="cell-sub truncate" style={{ maxWidth: 280 }}>
                      {displayTarget(r.url)}
                    </span>
                  </div>
                </td>
                <td>
                  <StatusBadge status={r.current_status} />
                  {!r.is_enabled && <span className="cell-sub"> · paused</span>}
                </td>
                <td className="num">{formatMs(r.last_response_time_ms)}</td>
                <td>
                  <RelativeTime iso={r.last_checked_at} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="chart-note">
        {total > resources.length
          ? `Showing ${formatCount(resources.length)} of ${formatCount(total)} services (most severe first within the first ${SERVICE_LIST_LIMIT}). `
          : `All ${formatCount(total)} services shown. `}
        <Link to="/resources">Open the full list</Link>
      </p>
    </div>
  );
}

export function OverviewPage() {
  const queryClient = useQueryClient();
  const overview = useOverview();
  const services = useServiceList();
  const active = useActiveIncidents();
  const resolved = useRecentlyResolved();
  const system = useSystemStatus();

  const refreshing = [overview, services, active, resolved].some((q) => q.isFetching);
  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: keys.overview });
    void queryClient.invalidateQueries({ queryKey: keys.resources });
    void queryClient.invalidateQueries({ queryKey: keys.incidents });
  };

  const header = (
    <PageHeader
      title="Overview"
      subtitle="Live health of everything CloudPulse monitors"
      actions={
        <>
          <Refreshing active={refreshing} />
          <Button small onClick={refreshAll}>
            <RefreshCw size={14} aria-hidden="true" /> Refresh
          </Button>
        </>
      }
    />
  );

  if (overview.isPending) {
    return (
      <div className="page">
        {header}
        <LoadingState label="Loading overview…" />
      </div>
    );
  }
  if (overview.isError) {
    return (
      <div className="page">
        {header}
        <ErrorState
          error={overview.error}
          onRetry={() => void overview.refetch()}
          retrying={overview.isFetching}
        />
      </div>
    );
  }

  const data = overview.data;
  const { by_status: counts, total } = data.resources;

  if (total === 0) {
    return (
      <div className="page">
        {header}
        <EmptyState
          icon={Server}
          title="No resources are being monitored yet"
          action={
            <Link to="/resources?new=1">
              <Button variant="primary">
                <Plus size={16} aria-hidden="true" /> Add your first resource
              </Button>
            </Link>
          }
        >
          Register an HTTP service and CloudPulse will check it on a schedule, record its history,
          and open incidents when it misbehaves.
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="page">
      {header}

      <section className="grid-stats" aria-label="System summary">
        <Stat
          label="Resources"
          value={total}
          icon={Server}
          tone="info"
          to="/resources"
          hint={`${data.resources.disabled} paused`}
        />
        <Stat
          label="Healthy"
          value={counts.HEALTHY}
          icon={CircleCheck}
          tone="healthy"
          to="/resources?status=HEALTHY"
        />
        <Stat
          label="Degraded"
          value={counts.DEGRADED}
          icon={TriangleAlert}
          tone="degraded"
          to="/resources?status=DEGRADED"
        />
        <Stat
          label="Down"
          value={counts.DOWN}
          icon={CircleX}
          tone="down"
          to="/resources?status=DOWN"
        />
        <Stat
          label="Unknown"
          value={counts.UNKNOWN}
          icon={CircleHelp}
          tone="unknown"
          to="/resources?status=UNKNOWN"
        />
        <Stat
          label="Active incidents"
          value={data.incidents.active}
          icon={ShieldAlert}
          tone={data.incidents.active > 0 ? 'down' : 'healthy'}
          to="/incidents?state=active"
          hint={`${data.incidents.open} open · ${data.incidents.acknowledged} acknowledged`}
        />
        <Stat
          label="Firing rules"
          value={data.alert_rules.firing}
          icon={Flame}
          tone={data.alert_rules.firing > 0 ? 'down' : 'healthy'}
          to="/alerts?state=FIRING"
          hint={`of ${data.alert_rules.total} rules`}
        />
      </section>

      <div className="grid-main-side">
        <div className="stack" style={{ gap: 'var(--space-5)' }}>
          <section className="card" aria-labelledby="dist-title">
            <div className="card__header">
              <h2 id="dist-title">Health distribution</h2>
              <span className="card__subtitle">
                Latest status of all {formatCount(total)} resources
              </span>
            </div>
            <div className="card__body">
              <HealthDistribution counts={counts} total={total} />
            </div>
          </section>

          <section className="section" aria-labelledby="services-title">
            <div className="section__header">
              <h2 id="services-title">
                <Activity size={16} aria-hidden="true" style={{ verticalAlign: '-2px' }} /> Service
                status
              </h2>
            </div>
            {services.isPending ? (
              <LoadingState label="Loading services…" />
            ) : services.isError ? (
              <ErrorState error={services.error} onRetry={() => void services.refetch()} />
            ) : (
              <ServiceList resources={services.data.items} total={services.data.total} />
            )}
          </section>
        </div>

        <div className="stack" style={{ gap: 'var(--space-5)' }}>
          <section className="card" aria-labelledby="active-title">
            <div className="card__header">
              <h2 id="active-title">Active incidents</h2>
              <Link to="/incidents?state=active">View all</Link>
            </div>
            <div className="card__body">
              {active.isPending ? (
                <LoadingState label="Loading incidents…" />
              ) : active.isError ? (
                <ErrorState error={active.error} onRetry={() => void active.refetch()} />
              ) : active.data.items.length === 0 ? (
                <p className="muted">
                  <CircleCheck
                    size={16}
                    aria-hidden="true"
                    style={{ verticalAlign: '-3px', color: 'var(--healthy)' }}
                  />{' '}
                  No active incidents.
                </p>
              ) : (
                <IncidentCompactList items={active.data.items} />
              )}
            </div>
          </section>

          <section className="card" aria-labelledby="recent-title">
            <div className="card__header">
              <h2 id="recent-title">Recently resolved</h2>
              <Link to="/incidents?state=RESOLVED">View all</Link>
            </div>
            <div className="card__body">
              {resolved.isPending ? (
                <LoadingState label="Loading…" />
              ) : resolved.isError ? (
                <ErrorState error={resolved.error} onRetry={() => void resolved.refetch()} />
              ) : resolved.data.items.length === 0 ? (
                <p className="muted">No incidents have been resolved yet.</p>
              ) : (
                <IncidentCompactList items={resolved.data.items} />
              )}
            </div>
          </section>

          <section className="card" aria-labelledby="system-title">
            <div className="card__header">
              <h2 id="system-title">Platform</h2>
            </div>
            <div className="card__body stack">
              <dl className="kv" style={{ gridTemplateColumns: '1fr 1fr' }}>
                <div className="kv__item">
                  <dt>API</dt>
                  <dd>
                    {system.data?.apiReachable
                      ? 'Reachable'
                      : system.data
                        ? 'Unreachable'
                        : 'Checking…'}
                  </dd>
                </div>
                <div className="kv__item">
                  <dt>Database</dt>
                  <dd>
                    {system.data?.database === 'ok'
                      ? 'Ready'
                      : system.data?.database === 'unavailable'
                        ? 'Unavailable'
                        : 'Unknown'}
                  </dd>
                </div>
              </dl>
              <p className="chart-note">
                Alert rules: {data.alert_rules.enabled} enabled of {data.alert_rules.total}. Counts
                are computed by the API over every resource, not just the list shown.
              </p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

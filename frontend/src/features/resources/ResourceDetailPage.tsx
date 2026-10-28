import { Pause, Pencil, Play, Plus, RefreshCw, Trash2, Zap } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { AvailabilityChart, LatencyChart } from '../../components/charts/TimeSeriesCharts.tsx';
import {
  AlertStateBadge,
  EnabledBadge,
  SeverityBadge,
  StatusBadge,
} from '../../components/ui/Badges.tsx';
import { Button } from '../../components/ui/Button.tsx';
import { ConfirmDialog } from '../../components/ui/Dialog.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import {
  ErrorState,
  LoadingState,
  Notice,
  NotFoundState,
  Refreshing,
} from '../../components/ui/States.tsx';
import { DateTime, RelativeTime } from '../../components/ui/Time.tsx';
import { useToast } from '../../components/ui/useToast.ts';
import { errorMessage, isApiError } from '../../lib/api/client.ts';
import type { Resource } from '../../lib/api/types.ts';
import { displayTarget, formatInterval, formatMs } from '../../lib/utils/format.ts';
import { PERIODS, parsePeriod, periodById } from '../../lib/utils/period.ts';
import { describeTrigger } from '../../lib/utils/rules.ts';
import { RuleFormModal } from '../alerts/RuleFormModal.tsx';
import { useResourceRules } from '../alerts/hooks.ts';
import { IncidentCompactList } from '../incidents/IncidentList.tsx';
import { useIncidentList } from '../incidents/hooks.ts';
import { HistoryTable } from '../metrics/HistoryTable.tsx';
import { MetricsSummary } from '../metrics/MetricsSummary.tsx';
import { PeriodSelect } from '../metrics/PeriodSelect.tsx';
import { useSummary, useTimeseries } from '../metrics/hooks.ts';
import { useOverview } from '../overview/hooks.ts';
import { ResourceFormModal } from './ResourceFormModal.tsx';
import { useDeleteResource, useResource } from './hooks.ts';
import { useResourceActions } from './useResourceActions.ts';

function Header({ resource }: { resource: Resource }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { runCheck, toggleEnabled, checking, toggling } = useResourceActions(resource);
  const remove = useDeleteResource();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const confirmDelete = () =>
    remove.mutate(resource.id, {
      onSuccess: () => {
        toast.success(`Deleted “${resource.name}”.`);
        navigate('/resources', { replace: true });
      },
      onError: (error) => {
        toast.error(errorMessage(error));
        setDeleting(false);
      },
    });

  return (
    <>
      <PageHeader
        title={resource.name}
        back={{ to: '/resources', label: 'All resources' }}
        subtitle={
          <span className="row">
            <StatusBadge status={resource.current_status} />
            <EnabledBadge enabled={resource.is_enabled} />
            <span
              className="mono truncate"
              style={{ maxWidth: 360 }}
              title="Host and path only; query strings are never displayed"
            >
              {displayTarget(resource.url)}
            </span>
          </span>
        }
        actions={
          <>
            <Button
              onClick={runCheck}
              loading={checking}
              disabled={!resource.is_enabled}
              title={resource.is_enabled ? undefined : 'Enable monitoring to run checks'}
            >
              {!checking && <Zap size={15} aria-hidden="true" />} Check now
            </Button>
            <Button onClick={toggleEnabled} loading={toggling}>
              {!toggling &&
                (resource.is_enabled ? (
                  <Pause size={15} aria-hidden="true" />
                ) : (
                  <Play size={15} aria-hidden="true" />
                ))}
              {resource.is_enabled ? 'Disable' : 'Enable'}
            </Button>
            <Button onClick={() => setEditing(true)}>
              <Pencil size={15} aria-hidden="true" /> Edit
            </Button>
            <Button variant="danger" onClick={() => setDeleting(true)}>
              <Trash2 size={15} aria-hidden="true" /> Delete
            </Button>
          </>
        }
      />
      <ResourceFormModal open={editing} resource={resource} onClose={() => setEditing(false)} />
      <ConfirmDialog
        open={deleting}
        title={`Delete ${resource.name}?`}
        confirmLabel="Delete resource"
        danger
        pending={remove.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(false)}
      >
        <p>
          This permanently deletes the resource, <strong>all of its monitoring history</strong> and
          its alert rules. Active incidents are resolved, and incidents it caused are kept with the
          resource name for reference.
        </p>
        <p className="muted">This cannot be undone.</p>
      </ConfirmDialog>
    </>
  );
}

function CurrentState({ resource }: { resource: Resource }) {
  return (
    <section className="card" aria-labelledby="state-title">
      <div className="card__header">
        <h2 id="state-title">Current state</h2>
      </div>
      <div className="card__body">
        <dl className="kv">
          <div className="kv__item">
            <dt>Latest response</dt>
            <dd>
              {formatMs(resource.last_response_time_ms)}
              {resource.last_http_status_code !== null && (
                <span className="cell-sub">HTTP {resource.last_http_status_code}</span>
              )}
            </dd>
          </div>
          <div className="kv__item">
            <dt>Last checked</dt>
            <dd>
              <RelativeTime iso={resource.last_checked_at} />
              <span className="cell-sub">
                <DateTime iso={resource.last_checked_at} empty="" />
              </span>
            </dd>
          </div>
          <div className="kv__item">
            <dt>Next check</dt>
            <dd>
              {resource.is_enabled ? (
                <RelativeTime iso={resource.next_check_at} empty="pending" />
              ) : (
                'Monitoring disabled'
              )}
            </dd>
          </div>
          <div className="kv__item">
            <dt>Check settings</dt>
            <dd>
              every {formatInterval(resource.check_interval_seconds)}, {resource.timeout_seconds}s
              timeout, expects HTTP {resource.expected_status_code}
            </dd>
          </div>
          {resource.last_error && (
            <div className="kv__item" style={{ gridColumn: '1 / -1' }}>
              <dt>Last error</dt>
              <dd>{resource.last_error}</dd>
            </div>
          )}
        </dl>
      </div>
    </section>
  );
}

function Charts({
  resourceId,
  period,
}: {
  resourceId: string;
  period: ReturnType<typeof parsePeriod>;
}) {
  const summary = useSummary(resourceId, period);
  const series = useTimeseries(resourceId, period);
  return (
    <>
      <section className="section" aria-labelledby="summary-title">
        <h2 id="summary-title">Summary · {periodById(period).label.toLowerCase()}</h2>
        {summary.isPending ? (
          <LoadingState label="Loading summary…" />
        ) : summary.isError ? (
          <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
        ) : (
          <MetricsSummary summary={summary.data} />
        )}
      </section>
      <div className="grid-2">
        <section className="card" aria-labelledby="latency-title">
          <div className="card__header">
            <h2 id="latency-title">Response time</h2>
            <span className="card__subtitle">
              {series.data ? `${series.data.interval} buckets` : ''}
            </span>
          </div>
          <div className="card__body stack">
            {series.isPending ? (
              <LoadingState label="Loading chart…" />
            ) : series.isError ? (
              <ErrorState error={series.error} onRetry={() => void series.refetch()} />
            ) : (
              <>
                <LatencyChart series={series.data} />
                <p className="chart-note">
                  Gaps mean no observations in that bucket, not downtime.
                </p>
              </>
            )}
          </div>
        </section>
        <section className="card" aria-labelledby="avail-title">
          <div className="card__header">
            <h2 id="avail-title">Availability</h2>
            <span className="card__subtitle">check-based</span>
          </div>
          <div className="card__body stack">
            {series.isPending ? (
              <LoadingState label="Loading chart…" />
            ) : series.isError ? (
              <ErrorState error={series.error} onRetry={() => void series.refetch()} />
            ) : (
              <>
                <AvailabilityChart series={series.data} />
                <p className="chart-note">
                  Percent of checks in each bucket that were healthy. Not continuous uptime.
                </p>
              </>
            )}
          </div>
        </section>
      </div>
    </>
  );
}

function RulesPanel({ resource }: { resource: Resource }) {
  const rules = useResourceRules(resource.id);
  const overview = useOverview();
  const [creating, setCreating] = useState(false);
  const limit = overview.data?.limits.max_rules_per_resource;
  return (
    <section className="card" aria-labelledby="rules-title">
      <div className="card__header">
        <h2 id="rules-title">Alert rules</h2>
        <Button small onClick={() => setCreating(true)}>
          <Plus size={14} aria-hidden="true" /> Add rule
        </Button>
      </div>
      <div className="card__body stack">
        {rules.isPending ? (
          <LoadingState label="Loading rules…" />
        ) : rules.isError ? (
          <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />
        ) : rules.data.items.length === 0 ? (
          <p className="muted">No alert rules on this resource yet.</p>
        ) : (
          <ul className="stack" style={{ gap: 0 }}>
            {rules.data.items.map((rule) => (
              <li
                key={rule.id}
                className="cell-title"
                style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}
              >
                <span className="row" style={{ justifyContent: 'space-between' }}>
                  <strong>{rule.name}</strong>
                  <span className="row">
                    <SeverityBadge severity={rule.severity} />
                    <AlertStateBadge state={rule.current_state} />
                  </span>
                </span>
                <span className="cell-sub">
                  {describeTrigger(rule)}
                  {!rule.is_enabled && ' · disabled'}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="chart-note">
          {rules.data && limit !== undefined ? `${rules.data.total} of ${limit} rules used. ` : ''}
          <Link to={`/alerts?resource=${resource.id}`}>Manage rules</Link>
        </p>
      </div>
      <RuleFormModal open={creating} resourceId={resource.id} onClose={() => setCreating(false)} />
    </section>
  );
}

function IncidentsPanel({ resourceId }: { resourceId: string }) {
  const incidents = useIncidentList({ resource_id: resourceId });
  const items = incidents.data?.pages[0]?.items.slice(0, 5) ?? [];
  return (
    <section className="card" aria-labelledby="inc-title">
      <div className="card__header">
        <h2 id="inc-title">Recent incidents</h2>
        <Link to={`/incidents?state=all&resource=${resourceId}`}>View all</Link>
      </div>
      <div className="card__body">
        {incidents.isPending ? (
          <LoadingState label="Loading incidents…" />
        ) : incidents.isError ? (
          <ErrorState error={incidents.error} onRetry={() => void incidents.refetch()} />
        ) : items.length === 0 ? (
          <p className="muted">No incidents for this resource.</p>
        ) : (
          <IncidentCompactList items={items} />
        )}
      </div>
    </section>
  );
}

export function ResourceDetailPage() {
  const { id = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const period = parsePeriod(params.get('period'));
  const resource = useResource(id);

  if (resource.isPending) {
    return (
      <div className="page">
        <PageHeader title="Resource" back={{ to: '/resources', label: 'All resources' }} />
        <LoadingState label="Loading resource…" />
      </div>
    );
  }
  if (resource.isError) {
    return (
      <div className="page">
        <PageHeader title="Resource" back={{ to: '/resources', label: 'All resources' }} />
        {isApiError(resource.error, 'not_found') ? (
          <NotFoundState
            what="Resource"
            action={
              <Link to="/resources">
                <Button>Back to resources</Button>
              </Link>
            }
          />
        ) : (
          <ErrorState
            error={resource.error}
            onRetry={() => void resource.refetch()}
            retrying={resource.isFetching}
          />
        )}
      </div>
    );
  }

  const data = resource.data;
  return (
    <div className="page">
      <Header resource={data} />
      {!data.is_enabled && (
        <Notice tone="warning" title="Monitoring is disabled">
          No new checks run while disabled. The history below is kept, and the status shows the last
          observation.
        </Notice>
      )}

      <CurrentState resource={data} />

      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2>Performance</h2>
        <span className="row">
          <Refreshing active={resource.isFetching} />
          <PeriodSelect
            value={period}
            onChange={(next) =>
              setParams(
                (current) => {
                  const nextParams = new URLSearchParams(current);
                  nextParams.set('period', next);
                  return nextParams;
                },
                { replace: true },
              )
            }
          />
          <Button
            small
            iconOnly
            aria-label="Refresh resource data"
            onClick={() => void resource.refetch()}
          >
            <RefreshCw size={14} aria-hidden="true" />
          </Button>
        </span>
      </div>

      <Charts resourceId={data.id} period={period} />

      <div className="grid-main-side">
        <section className="section" aria-labelledby="history-title">
          <h2 id="history-title">
            Check history · {PERIODS.find((p) => p.id === period)?.label.toLowerCase()}
          </h2>
          <HistoryTable resourceId={data.id} period={period} />
        </section>
        <div className="stack" style={{ gap: 'var(--space-4)' }}>
          <RulesPanel resource={data} />
          <IncidentsPanel resourceId={data.id} />
        </div>
      </div>
    </div>
  );
}

import { Eye } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { IncidentStatusBadge, SeverityBadge, StatusBadge } from '../../components/ui/Badges.tsx';
import { Button } from '../../components/ui/Button.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { ErrorState, LoadingState, Notice, NotFoundState } from '../../components/ui/States.tsx';
import { DateTime, RelativeTime } from '../../components/ui/Time.tsx';
import { useToast } from '../../components/ui/useToast.ts';
import { errorMessage, isApiError } from '../../lib/api/client.ts';
import { formatDuration, formatMs } from '../../lib/utils/format.ts';
import { RESOLUTION_LABELS, RULE_TYPE_LABELS } from '../../lib/utils/rules.ts';
import { Timeline } from './Timeline.tsx';
import { useAcknowledge, useIncident, useIncidentEvents, useTriggeringCheck } from './hooks.ts';

export function IncidentDetailPage() {
  const { id = '' } = useParams();
  const toast = useToast();
  const incident = useIncident(id);
  const events = useIncidentEvents(id);
  const trigger = useTriggeringCheck(incident.data);
  const acknowledge = useAcknowledge();

  const back = { to: '/incidents', label: 'All incidents' };

  if (incident.isPending) {
    return (
      <div className="page">
        <PageHeader title="Incident" back={back} />
        <LoadingState label="Loading incident…" />
      </div>
    );
  }
  if (incident.isError) {
    return (
      <div className="page">
        <PageHeader title="Incident" back={back} />
        {isApiError(incident.error, 'not_found') ? (
          <NotFoundState
            what="Incident"
            action={
              <Link to="/incidents">
                <Button>Back to incidents</Button>
              </Link>
            }
          />
        ) : (
          <ErrorState
            error={incident.error}
            onRetry={() => void incident.refetch()}
            retrying={incident.isFetching}
          />
        )}
      </div>
    );
  }

  const data = incident.data;

  const onAcknowledge = () =>
    acknowledge.mutate(data.id, {
      onSuccess: () => toast.success('Incident acknowledged.'),
      // 409: it resolved while the request was in flight. The refetch shows the real state.
      onError: (error) => toast.error(errorMessage(error)),
    });

  return (
    <div className="page">
      <PageHeader
        title={data.title}
        documentTitle={`Incident · ${data.resource_name}`}
        back={back}
        subtitle={
          <span className="row">
            <SeverityBadge severity={data.severity} />
            <IncidentStatusBadge status={data.status} />
          </span>
        }
        actions={
          data.status === 'OPEN' ? (
            <Button variant="primary" onClick={onAcknowledge} loading={acknowledge.isPending}>
              <Eye size={16} aria-hidden="true" /> Acknowledge
            </Button>
          ) : undefined
        }
      />

      {data.status === 'OPEN' && (
        <Notice tone="info">
          Acknowledging records that someone has looked at this incident. It does not resolve it:
          the incident resolves automatically when the rule&apos;s recovery condition is met. No
          user identity is attached because CloudPulse has no authentication yet.
        </Notice>
      )}
      {data.status === 'ACKNOWLEDGED' && (
        <Notice tone="info">
          Acknowledged, still active. It resolves automatically on recovery.
        </Notice>
      )}

      <div className="grid-main-side">
        <section className="card" aria-labelledby="details-title">
          <div className="card__header">
            <h2 id="details-title">Details</h2>
          </div>
          <div className="card__body">
            <dl className="kv">
              <div className="kv__item">
                <dt>Resource</dt>
                <dd>
                  {data.resource_id ? (
                    <Link to={`/resources/${data.resource_id}`}>{data.resource_name}</Link>
                  ) : (
                    <>
                      {data.resource_name} <span className="subtle">(deleted)</span>
                    </>
                  )}
                </dd>
              </div>
              <div className="kv__item">
                <dt>Rule</dt>
                <dd>
                  {data.alert_rule_id && data.resource_id ? (
                    <Link to={`/alerts?resource=${data.resource_id}`}>{data.rule_name}</Link>
                  ) : (
                    <>
                      {data.rule_name} <span className="subtle">(deleted)</span>
                    </>
                  )}
                  <span className="cell-sub">{RULE_TYPE_LABELS[data.rule_type]}</span>
                </dd>
              </div>
              <div className="kv__item">
                <dt>Detected</dt>
                <dd>
                  <DateTime iso={data.triggered_at} />
                  <span className="cell-sub">
                    <RelativeTime iso={data.triggered_at} />
                  </span>
                </dd>
              </div>
              <div className="kv__item">
                <dt>Acknowledged</dt>
                <dd>
                  <DateTime iso={data.acknowledged_at} empty="Not acknowledged" />
                </dd>
              </div>
              <div className="kv__item">
                <dt>Resolved</dt>
                <dd>
                  <DateTime iso={data.resolved_at} empty="Still active" />
                  {data.resolved_at && (
                    <span className="cell-sub">
                      after {formatDuration(data.triggered_at, data.resolved_at)}
                    </span>
                  )}
                </dd>
              </div>
              <div className="kv__item">
                <dt>Resolution</dt>
                <dd>{data.resolution_reason ? RESOLUTION_LABELS[data.resolution_reason] : '—'}</dd>
              </div>
              <div className="kv__item">
                <dt>Last matching check</dt>
                <dd>
                  <RelativeTime iso={data.last_observed_at} />
                </dd>
              </div>
            </dl>
          </div>
        </section>

        <div className="stack" style={{ gap: 'var(--space-4)' }}>
          <section className="card" aria-labelledby="trigger-title">
            <div className="card__header">
              <h2 id="trigger-title">Triggering check</h2>
            </div>
            <div className="card__body">
              {!data.resource_id ? (
                <p className="muted">The resource was deleted, so its history is gone.</p>
              ) : !data.opened_by_check_id ? (
                <p className="muted">
                  The measurement that opened this incident is no longer stored.
                </p>
              ) : trigger.isPending ? (
                <LoadingState label="Looking up the check…" />
              ) : trigger.isError ? (
                <p className="muted">Could not load the triggering check.</p>
              ) : trigger.data === null || trigger.data === undefined ? (
                <p className="muted">
                  That measurement is no longer available (it is past the history retention period).
                </p>
              ) : (
                <dl className="kv" style={{ gridTemplateColumns: '1fr 1fr' }}>
                  <div className="kv__item">
                    <dt>Result</dt>
                    <dd>
                      <StatusBadge status={trigger.data.status} />
                    </dd>
                  </div>
                  <div className="kv__item">
                    <dt>HTTP status</dt>
                    <dd>{trigger.data.http_status_code ?? 'no response'}</dd>
                  </div>
                  <div className="kv__item">
                    <dt>Response time</dt>
                    <dd>{formatMs(trigger.data.response_time_ms)}</dd>
                  </div>
                  <div className="kv__item">
                    <dt>Source</dt>
                    <dd>{trigger.data.source === 'MANUAL' ? 'Manual check' : 'Scheduled'}</dd>
                  </div>
                  {trigger.data.error_summary && (
                    <div className="kv__item" style={{ gridColumn: '1 / -1' }}>
                      <dt>Error</dt>
                      <dd>{trigger.data.error_summary}</dd>
                    </div>
                  )}
                </dl>
              )}
            </div>
          </section>

          <section className="card" aria-labelledby="timeline-title">
            <div className="card__header">
              <h2 id="timeline-title">Timeline</h2>
            </div>
            <div className="card__body">
              {events.isPending ? (
                <LoadingState label="Loading timeline…" />
              ) : events.isError ? (
                <ErrorState error={events.error} onRetry={() => void events.refetch()} />
              ) : (
                <Timeline events={events.data.items} />
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

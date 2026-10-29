import { Pause, Pencil, Play, Plus, RefreshCw, Search, Trash2, Zap } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { EnabledBadge, StatusBadge } from '../../components/ui/Badges.tsx';
import { Button } from '../../components/ui/Button.tsx';
import { ConfirmDialog } from '../../components/ui/Dialog.tsx';
import { FilterSelect } from '../../components/ui/Fields.tsx';
import { OffsetPager } from '../../components/ui/Pager.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, ErrorState, LoadingState, Refreshing } from '../../components/ui/States.tsx';
import { useToast } from '../../components/ui/useToast.ts';
import { RelativeTime } from '../../components/ui/Time.tsx';
import { errorMessage } from '../../lib/api/client.ts';
import type { Resource, ResourceStatus } from '../../lib/api/types.ts';
import { displayTarget, formatInterval, formatMs } from '../../lib/utils/format.ts';
import { ResourceFormModal } from './ResourceFormModal.tsx';
import { useDeleteResource, useResources } from './hooks.ts';
import { useResourceActions } from './useResourceActions.ts';

const PAGE_SIZE = 25;
const STATUSES: ResourceStatus[] = ['HEALTHY', 'DEGRADED', 'DOWN', 'UNKNOWN'];

function parseStatus(value: string | null): ResourceStatus | undefined {
  return STATUSES.includes(value as ResourceStatus) ? (value as ResourceStatus) : undefined;
}

function ResourceRow({
  resource,
  onEdit,
  onDelete,
}: {
  resource: Resource;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { runCheck, toggleEnabled, checking, toggling } = useResourceActions(resource);
  const name = resource.name;
  return (
    <tr>
      <td data-label="Name">
        <div className="cell-title">
          <Link to={`/resources/${resource.id}`}>{name}</Link>
          {resource.description && (
            <span className="cell-sub truncate" style={{ maxWidth: 260 }}>
              {resource.description}
            </span>
          )}
        </div>
      </td>
      <td data-label="Status">
        <StatusBadge status={resource.current_status} />
      </td>
      <td data-label="Target">
        <span
          className="mono truncate"
          style={{ display: 'block', maxWidth: 240 }}
          title="Host and path only"
        >
          {displayTarget(resource.url)}
        </span>
      </td>
      <td className="num nowrap" data-label="Interval">
        {formatInterval(resource.check_interval_seconds)}
      </td>
      <td className="num nowrap" data-label="Latest response">
        {formatMs(resource.last_response_time_ms)}
      </td>
      <td className="nowrap" data-label="Last checked">
        <RelativeTime iso={resource.last_checked_at} />
      </td>
      <td data-label="Monitoring">
        <EnabledBadge enabled={resource.is_enabled} />
      </td>
      <td data-label="">
        <div className="actions">
          <Button
            small
            iconOnly
            aria-label={`Check ${name} now`}
            title={resource.is_enabled ? 'Run a check now' : 'Enable monitoring to run checks'}
            onClick={runCheck}
            loading={checking}
            disabled={!resource.is_enabled}
          >
            {!checking && <Zap size={14} aria-hidden="true" />}
          </Button>
          <Button
            small
            iconOnly
            aria-label={
              resource.is_enabled ? `Disable monitoring of ${name}` : `Enable monitoring of ${name}`
            }
            title={resource.is_enabled ? 'Disable monitoring' : 'Enable monitoring'}
            onClick={toggleEnabled}
            loading={toggling}
          >
            {!toggling &&
              (resource.is_enabled ? (
                <Pause size={14} aria-hidden="true" />
              ) : (
                <Play size={14} aria-hidden="true" />
              ))}
          </Button>
          <Button small iconOnly aria-label={`Edit ${name}`} title="Edit" onClick={onEdit}>
            <Pencil size={14} aria-hidden="true" />
          </Button>
          <Button
            small
            iconOnly
            variant="danger"
            aria-label={`Delete ${name}`}
            title="Delete"
            onClick={onDelete}
          >
            <Trash2 size={14} aria-hidden="true" />
          </Button>
        </div>
      </td>
    </tr>
  );
}

export function ResourcesPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const q = params.get('q') ?? '';
  const status = parseStatus(params.get('status'));
  const enabledParam = params.get('enabled');
  const isEnabled = enabledParam === 'true' ? true : enabledParam === 'false' ? false : undefined;
  const offset = Math.max(0, Number(params.get('offset')) || 0);

  const update = useCallback(
    (changes: Record<string, string | null>) =>
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          for (const [key, value] of Object.entries(changes)) {
            if (value === null || value === '') next.delete(key);
            else next.set(key, value);
          }
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );

  // What is typed is held as a draft and pushed to the URL after a short pause; with no draft
  // the box shows the URL's value.
  const [draft, setDraft] = useState<string | null>(null);
  const search = draft ?? q;
  useEffect(() => {
    if (draft === null) return;
    const timer = window.setTimeout(() => {
      update({ q: draft || null, offset: null });
      setDraft(null);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [draft, update]);

  const list = useResources({
    limit: PAGE_SIZE,
    offset,
    q: q || undefined,
    current_status: status,
    is_enabled: isEnabled,
  });
  const remove = useDeleteResource();

  const [formOpen, setFormOpen] = useState(params.get('new') === '1');
  const [editing, setEditing] = useState<Resource | null>(null);
  const [deleting, setDeleting] = useState<Resource | null>(null);

  const closeForm = () => {
    setFormOpen(false);
    setEditing(null);
    if (params.get('new')) update({ new: null });
  };

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(deleting.id, {
      onSuccess: () => {
        toast.success(`Deleted “${deleting.name}”.`);
        setDeleting(null);
      },
      onError: (error) => {
        toast.error(errorMessage(error));
        setDeleting(null);
      },
    });
  };

  const filtered = Boolean(q || status || isEnabled !== undefined);

  return (
    <div className="page">
      <PageHeader
        title="Resources"
        subtitle="HTTP services CloudPulse checks on a schedule"
        actions={
          <>
            <Refreshing active={list.isFetching && !list.isPending} />
            <Button small onClick={() => void list.refetch()} aria-label="Refresh resources">
              <RefreshCw size={14} aria-hidden="true" /> Refresh
            </Button>
            <Button variant="primary" onClick={() => setFormOpen(true)}>
              <Plus size={16} aria-hidden="true" /> Add resource
            </Button>
          </>
        }
      />

      <div className="toolbar" role="search" aria-label="Filter resources">
        <div className="field search">
          <label className="field__label" htmlFor="resource-search">
            Search by name
          </label>
          <div style={{ position: 'relative' }}>
            <Search
              size={15}
              aria-hidden="true"
              style={{ position: 'absolute', left: 11, top: 12, color: 'var(--text-subtle)' }}
            />
            <input
              id="resource-search"
              type="search"
              className="input"
              style={{ paddingLeft: 34 }}
              placeholder="Search resources"
              value={search}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={100}
            />
          </div>
        </div>
        <FilterSelect
          label="Status"
          value={status ?? ''}
          onChange={(e) => update({ status: e.target.value || null, offset: null })}
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.charAt(0) + s.slice(1).toLowerCase()}
            </option>
          ))}
        </FilterSelect>
        <FilterSelect
          label="Monitoring"
          value={enabledParam ?? ''}
          onChange={(e) => update({ enabled: e.target.value || null, offset: null })}
        >
          <option value="">Enabled and disabled</option>
          <option value="true">Enabled only</option>
          <option value="false">Disabled only</option>
        </FilterSelect>
        {filtered && (
          <Button variant="ghost" onClick={() => setParams({}, { replace: true })}>
            Clear filters
          </Button>
        )}
      </div>

      {list.isPending ? (
        <LoadingState label="Loading resources…" />
      ) : list.isError && !list.data ? (
        <ErrorState
          error={list.error}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
        />
      ) : list.data.total === 0 ? (
        filtered ? (
          <EmptyState title="No resources match these filters">
            Try a different search or{' '}
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => setParams({}, { replace: true })}
            >
              clear the filters
            </button>
            .
          </EmptyState>
        ) : (
          <EmptyState
            title="No resources yet"
            action={
              <Button variant="primary" onClick={() => setFormOpen(true)}>
                <Plus size={16} aria-hidden="true" /> Add your first resource
              </Button>
            }
          >
            Add an HTTP endpoint to start monitoring it.
          </EmptyState>
        )
      ) : (
        <>
          {list.isError && (
            <ErrorState
              error={list.error}
              onRetry={() => void list.refetch()}
              title="Showing the last loaded data"
            />
          )}
          <div
            className="table-wrap table-wrap--stack"
            role="region"
            aria-label="Monitored resources"
            tabIndex={0}
          >
            <table className="table table--stack">
              <caption className="sr-only">Monitored resources</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Status</th>
                  <th scope="col">Target</th>
                  <th scope="col" className="num">
                    Interval
                  </th>
                  <th scope="col" className="num">
                    Latest response
                  </th>
                  <th scope="col">Last checked</th>
                  <th scope="col">Monitoring</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((resource) => (
                  <ResourceRow
                    key={resource.id}
                    resource={resource}
                    onEdit={() => setEditing(resource)}
                    onDelete={() => setDeleting(resource)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <OffsetPager
            offset={list.data.offset}
            limit={list.data.limit}
            total={list.data.total}
            noun="resources"
            onChange={(next) => update({ offset: next ? String(next) : null })}
          />
        </>
      )}

      <ResourceFormModal
        open={formOpen || editing !== null}
        resource={editing ?? undefined}
        onClose={closeForm}
        onSaved={(saved) => {
          if (!editing) navigate(`/resources/${saved.id}`);
        }}
      />

      <ConfirmDialog
        open={deleting !== null}
        title={`Delete ${deleting?.name ?? 'resource'}?`}
        confirmLabel="Delete resource"
        danger
        pending={remove.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      >
        <p>
          This permanently deletes the resource, <strong>all of its monitoring history</strong> and
          its alert rules. Active incidents are resolved, and incidents it caused are kept with the
          resource name for reference.
        </p>
        <p className="muted">This cannot be undone.</p>
      </ConfirmDialog>
    </div>
  );
}

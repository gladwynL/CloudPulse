import { RefreshCw } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { Button } from '../../components/ui/Button.tsx';
import { FilterSelect } from '../../components/ui/Fields.tsx';
import { LoadMore } from '../../components/ui/Pager.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, ErrorState, LoadingState, Refreshing } from '../../components/ui/States.tsx';
import type { AlertSeverity, IncidentStatus } from '../../lib/api/types.ts';
import { useResources } from '../resources/hooks.ts';
import { IncidentsTable } from './IncidentList.tsx';
import { useIncidentList, type IncidentFilters } from './hooks.ts';

type StateFilter = 'active' | 'all' | IncidentStatus;

const STATE_OPTIONS: { value: StateFilter; label: string }[] = [
  { value: 'active', label: 'Active (open or acknowledged)' },
  { value: 'OPEN', label: 'Open' },
  { value: 'ACKNOWLEDGED', label: 'Acknowledged' },
  { value: 'RESOLVED', label: 'Resolved' },
  { value: 'all', label: 'All incidents' },
];

function parseState(value: string | null): StateFilter {
  return STATE_OPTIONS.some((o) => o.value === value) ? (value as StateFilter) : 'active';
}

export function IncidentsPage() {
  const [params, setParams] = useSearchParams();
  const state = parseState(params.get('state'));
  const severityParam = params.get('severity');
  const severity: AlertSeverity | undefined =
    severityParam === 'WARNING' || severityParam === 'CRITICAL' ? severityParam : undefined;
  const resourceId = params.get('resource') ?? undefined;

  const update = (changes: Record<string, string | null>) =>
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
    );

  const filters: IncidentFilters = {
    ...(state === 'active' ? { active: true } : state === 'all' ? {} : { status: state }),
    severity,
    resource_id: resourceId,
  };
  const list = useIncidentList(filters);
  const resources = useResources({ limit: 200 });

  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  const filtered = state !== 'active' || severity !== undefined || resourceId !== undefined;

  return (
    <div className="page">
      <PageHeader
        title="Incidents"
        subtitle="Problems detected by alert rules, newest first"
        actions={
          <>
            <Refreshing active={list.isFetching && !list.isPending && !list.isFetchingNextPage} />
            <Button small onClick={() => void list.refetch()} aria-label="Refresh incidents">
              <RefreshCw size={14} aria-hidden="true" /> Refresh
            </Button>
          </>
        }
      />

      <div className="toolbar" role="search" aria-label="Filter incidents">
        <FilterSelect
          label="Show"
          value={state}
          onChange={(e) => update({ state: e.target.value === 'active' ? null : e.target.value })}
        >
          {STATE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </FilterSelect>
        <FilterSelect
          label="Severity"
          value={severity ?? ''}
          onChange={(e) => update({ severity: e.target.value || null })}
        >
          <option value="">All severities</option>
          <option value="CRITICAL">Critical</option>
          <option value="WARNING">Warning</option>
        </FilterSelect>
        <FilterSelect
          label="Resource"
          value={resourceId ?? ''}
          onChange={(e) => update({ resource: e.target.value || null })}
        >
          <option value="">All resources</option>
          {resources.data?.items.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </FilterSelect>
        {filtered && (
          <Button variant="ghost" onClick={() => setParams({}, { replace: true })}>
            Reset filters
          </Button>
        )}
      </div>

      {list.isPending ? (
        <LoadingState label="Loading incidents…" />
      ) : list.isError && !list.data ? (
        <ErrorState
          error={list.error}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
        />
      ) : items.length === 0 ? (
        <EmptyState
          title={
            state === 'active' && !filtered
              ? 'No active incidents'
              : 'No incidents match these filters'
          }
        >
          {state === 'active' && !filtered
            ? 'Everything being watched by an alert rule is currently fine.'
            : 'Try widening the filters, or show all incidents.'}
        </EmptyState>
      ) : (
        <>
          <IncidentsTable items={items} />
          <LoadMore
            shown={items.length}
            noun="incidents"
            hasMore={Boolean(list.hasNextPage)}
            loading={list.isFetchingNextPage}
            onLoadMore={() => void list.fetchNextPage()}
          />
        </>
      )}
    </div>
  );
}

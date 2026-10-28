import { useState } from 'react';
import { StatusBadge } from '../../components/ui/Badges.tsx';
import { FilterSelect } from '../../components/ui/Fields.tsx';
import { LoadMore } from '../../components/ui/Pager.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/States.tsx';
import { DateTime } from '../../components/ui/Time.tsx';
import type { CheckStatus, ErrorCategory } from '../../lib/api/types.ts';
import { formatMs } from '../../lib/utils/format.ts';
import type { PeriodId } from '../../lib/utils/period.ts';
import { useHistory } from './hooks.ts';

const CATEGORY_LABELS: Record<ErrorCategory, string> = {
  timeout: 'Timeout',
  dns: 'DNS failure',
  connection: 'Connection failed',
  tls: 'TLS error',
  blocked: 'Blocked by network policy',
  protocol: 'Protocol error',
  unexpected_status: 'Unexpected status',
  internal: 'Internal error',
};

const STATUS_FILTERS: CheckStatus[] = ['HEALTHY', 'DEGRADED', 'DOWN'];

export function HistoryTable({ resourceId, period }: { resourceId: string; period: PeriodId }) {
  const [status, setStatus] = useState<CheckStatus | undefined>();
  const history = useHistory(resourceId, period, status);
  const items = history.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="stack">
      <div className="toolbar">
        <FilterSelect
          label="Result"
          value={status ?? ''}
          onChange={(e) => setStatus((e.target.value || undefined) as CheckStatus | undefined)}
        >
          <option value="">All results</option>
          {STATUS_FILTERS.map((s) => (
            <option key={s} value={s}>
              {s.charAt(0) + s.slice(1).toLowerCase()}
            </option>
          ))}
        </FilterSelect>
      </div>
      {history.isPending ? (
        <LoadingState label="Loading check history…" />
      ) : history.isError && !history.data ? (
        <ErrorState
          error={history.error}
          onRetry={() => void history.refetch()}
          retrying={history.isFetching}
        />
      ) : items.length === 0 ? (
        <EmptyState title="No checks in this period">
          {status ? 'No checks with that result were recorded.' : 'Checks appear here as they run.'}
        </EmptyState>
      ) : (
        <>
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Check history, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Result</th>
                  <th scope="col" className="num">
                    Response time
                  </th>
                  <th scope="col" className="num">
                    HTTP
                  </th>
                  <th scope="col">Error</th>
                  <th scope="col">Source</th>
                </tr>
              </thead>
              <tbody>
                {items.map((check) => (
                  <tr key={check.id}>
                    <td className="nowrap">
                      <DateTime iso={check.checked_at} />
                    </td>
                    <td>
                      <StatusBadge status={check.status} />
                    </td>
                    <td className="num nowrap">{formatMs(check.response_time_ms)}</td>
                    <td className="num">{check.http_status_code ?? '—'}</td>
                    <td>
                      {check.error_category ? (
                        <div className="cell-title">
                          <span>{CATEGORY_LABELS[check.error_category]}</span>
                          {check.error_summary && (
                            <span className="cell-sub">{check.error_summary}</span>
                          )}
                        </div>
                      ) : (
                        <span className="subtle">—</span>
                      )}
                    </td>
                    <td>{check.source === 'MANUAL' ? 'Manual' : 'Scheduled'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LoadMore
            shown={items.length}
            noun="checks"
            hasMore={Boolean(history.hasNextPage)}
            loading={history.isFetchingNextPage}
            onLoadMore={() => void history.fetchNextPage()}
          />
        </>
      )}
    </div>
  );
}

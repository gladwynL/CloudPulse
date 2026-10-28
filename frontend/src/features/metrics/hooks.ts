import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api/endpoints.ts';
import type { CheckHistory, CheckStatus, TimeWindow } from '../../lib/api/types.ts';
import { polling } from '../../lib/polling.ts';
import { keys } from '../../lib/queryKeys.ts';
import { windowFor, type PeriodId } from '../../lib/utils/period.ts';

/** The window is computed when the request runs, so each poll covers "the last N hours" now. */
export function useSummary(id: string, period: PeriodId) {
  return useQuery({
    queryKey: keys.summary(id, period),
    queryFn: ({ signal }) => api.metricsSummary(id, windowFor(period), { signal }),
    refetchInterval: polling.charts,
  });
}

export function useTimeseries(id: string, period: PeriodId) {
  return useQuery({
    queryKey: keys.timeseries(id, period),
    queryFn: ({ signal }) => api.metricsTimeseries(id, windowFor(period), 'auto', { signal }),
    refetchInterval: polling.charts,
  });
}

export const HISTORY_PAGE_SIZE = 25;

interface HistoryParam {
  cursor: string;
  window: TimeWindow;
}

/**
 * Check history with cursor pagination. The first request fixes the window; later pages reuse the
 * window the server reported so the cursor and the filter stay consistent while you page.
 * Polling refreshes only when a single page is loaded, so paging deep never re-fetches every page.
 */
export function useHistory(id: string, period: PeriodId, status: CheckStatus | undefined) {
  return useInfiniteQuery<
    CheckHistory,
    Error,
    { pages: CheckHistory[] },
    readonly unknown[],
    HistoryParam | null
  >({
    queryKey: keys.history(id, period, status),
    initialPageParam: null,
    queryFn: ({ pageParam, signal }) => {
      const window = pageParam?.window ?? windowFor(period);
      return api.listChecks(
        id,
        { ...window, status, limit: HISTORY_PAGE_SIZE, cursor: pageParam?.cursor },
        { signal },
      );
    },
    getNextPageParam: (last) =>
      last.next_cursor ? { cursor: last.next_cursor, window: last.window } : undefined,
    refetchInterval: (query) =>
      (query.state.data?.pages.length ?? 0) > 1 ? false : polling.history,
  });
}

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api/endpoints.ts';
import type { Incident, IncidentList, IncidentListParams } from '../../lib/api/types.ts';
import { polling } from '../../lib/polling.ts';
import { keys } from '../../lib/queryKeys.ts';

export const INCIDENT_PAGE_SIZE = 25;

export type IncidentFilters = Omit<IncidentListParams, 'cursor' | 'limit'>;

/** Cursor-paginated list. Polls only the first page (see useHistory for the reasoning). */
export function useIncidentList(filters: IncidentFilters) {
  return useInfiniteQuery<
    IncidentList,
    Error,
    { pages: IncidentList[] },
    readonly unknown[],
    string | null
  >({
    queryKey: keys.incidentList(filters),
    initialPageParam: null,
    queryFn: ({ pageParam, signal }) =>
      api.listIncidents(
        { ...filters, limit: INCIDENT_PAGE_SIZE, cursor: pageParam ?? undefined },
        { signal },
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: (query) =>
      (query.state.data?.pages.length ?? 0) > 1 ? false : polling.incidents,
  });
}

export function useIncident(id: string) {
  return useQuery({
    queryKey: keys.incidentDetail(id),
    queryFn: ({ signal }) => api.getIncident(id, { signal }),
    // A resolved incident no longer changes, so it stops polling.
    refetchInterval: (query) =>
      query.state.data?.status === 'RESOLVED' ? false : polling.incidents,
  });
}

export function useIncidentEvents(id: string) {
  return useQuery({
    queryKey: keys.incidentEvents(id),
    queryFn: ({ signal }) => api.incidentEvents(id, { signal }),
    refetchInterval: polling.incidents,
  });
}

/**
 * The measurement that opened an incident. There is no "get check by id" endpoint, but the
 * incident's `triggered_at` equals that check's `checked_at`, so a 2 ms window around it finds the
 * row. Returns null if it is gone (resource deleted, or past retention).
 */
export function useTriggeringCheck(incident: Incident | undefined) {
  const resourceId = incident?.resource_id ?? null;
  const checkId = incident?.opened_by_check_id ?? null;
  return useQuery({
    queryKey: keys.triggeringCheck(incident?.id ?? 'none'),
    enabled: Boolean(incident && resourceId && checkId),
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      const triggered = new Date(incident!.triggered_at).getTime();
      const start = new Date(triggered).toISOString();
      const end = new Date(triggered + 2).toISOString();
      const history = await api.listChecks(resourceId!, { start, end, limit: 10 }, { signal });
      return history.items.find((item) => item.id === checkId) ?? null;
    },
  });
}

export function useAcknowledge() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.acknowledgeIncident(id),
    // On 409 the incident resolved meanwhile; refetching shows the real state either way.
    onSettled: (_data, _error, id) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.incident(id) }),
        queryClient.invalidateQueries({ queryKey: keys.incidents }),
        queryClient.invalidateQueries({ queryKey: keys.overview }),
      ]),
  });
}

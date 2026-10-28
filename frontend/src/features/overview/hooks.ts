import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api/endpoints.ts';
import { keys } from '../../lib/queryKeys.ts';
import { polling } from '../../lib/polling.ts';

/** Accurate system-wide totals, computed server-side (see GET /api/v1/overview). */
export function useOverview() {
  return useQuery({
    queryKey: keys.overview,
    queryFn: ({ signal }) => api.overview({ signal }),
    refetchInterval: polling.overview,
  });
}

/** How many services the overview lists. The total comes from the overview counts. */
export const SERVICE_LIST_LIMIT = 50;

export function useServiceList() {
  return useQuery({
    queryKey: keys.resourceList({ scope: 'overview', limit: SERVICE_LIST_LIMIT }),
    queryFn: ({ signal }) => api.listResources({ limit: SERVICE_LIST_LIMIT }, { signal }),
    refetchInterval: polling.overview,
  });
}

export function useActiveIncidents() {
  return useQuery({
    queryKey: keys.incidentList({ scope: 'overview', active: true }),
    queryFn: ({ signal }) => api.listIncidents({ active: true, limit: 5 }, { signal }),
    refetchInterval: polling.incidents,
  });
}

export function useRecentlyResolved() {
  return useQuery({
    queryKey: keys.incidentList({ scope: 'overview', resolved: true }),
    queryFn: ({ signal }) => api.listIncidents({ status: 'RESOLVED', limit: 5 }, { signal }),
    refetchInterval: polling.incidents,
  });
}

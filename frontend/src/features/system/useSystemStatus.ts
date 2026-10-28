import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api/endpoints.ts';
import { isApiError } from '../../lib/api/client.ts';
import { keys } from '../../lib/queryKeys.ts';
import { polling } from '../../lib/polling.ts';

export type Connectivity = 'checking' | 'online' | 'degraded' | 'offline';

export interface SystemProbe {
  apiReachable: boolean;
  /** `unknown` when the API itself is unreachable and readiness could not be asked. */
  database: 'ok' | 'unavailable' | 'unknown';
  checkedAt: number;
}

/** Never throws: an unreachable API is a result ("offline"), not an error state. */
async function probe(signal: AbortSignal): Promise<SystemProbe> {
  try {
    await api.health({ signal });
  } catch (error) {
    if (signal.aborted) throw error;
    return { apiReachable: false, database: 'unknown', checkedAt: Date.now() };
  }
  try {
    await api.ready({ signal });
    return { apiReachable: true, database: 'ok', checkedAt: Date.now() };
  } catch (error) {
    if (signal.aborted) throw error;
    return {
      apiReachable: true,
      database: isApiError(error, 'unavailable') ? 'unavailable' : 'unknown',
      checkedAt: Date.now(),
    };
  }
}

export function connectivityOf(probeResult: SystemProbe | undefined): Connectivity {
  if (!probeResult) return 'checking';
  if (!probeResult.apiReachable) return 'offline';
  return probeResult.database === 'ok' ? 'online' : 'degraded';
}

export function useSystemStatus() {
  const query = useQuery({
    queryKey: keys.system,
    queryFn: ({ signal }) => probe(signal),
    refetchInterval: polling.system,
    retry: false,
    staleTime: 0,
  });
  return { ...query, connectivity: connectivityOf(query.data) };
}

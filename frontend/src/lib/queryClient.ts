import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './api/client.ts';

/** Retry only failures that may be transient; a 404/409/422 will not fix itself. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (!(error instanceof ApiError)) return false;
  return (
    error.kind === 'network' ||
    error.kind === 'timeout' ||
    error.kind === 'unavailable' ||
    error.kind === 'server'
  );
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
        // Data is polled, so a few seconds of freshness avoids duplicate fetches on navigation.
        staleTime: 5_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: true,
        // Intervals pause while the tab is hidden: we never set refetchIntervalInBackground.
      },
      mutations: { retry: false },
    },
  });
}

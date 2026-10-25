import { config } from '../config.ts';

/** Outcome of calling one of the API's probe endpoints. */
export type ProbeState = 'operational' | 'unavailable' | 'unreachable';

export interface ProbeResult {
  state: ProbeState;
  httpStatus?: number;
}

const PROBE_TIMEOUT_MS = 5000;

/**
 * Call a probe endpoint (`/health` or `/ready`).
 * - 2xx → operational
 * - any other HTTP status → unavailable (API answered but reports a problem)
 * - network error / timeout → unreachable
 */
export async function probe(
  path: '/health' | '/ready',
  signal?: AbortSignal,
): Promise<ProbeResult> {
  const timeout = AbortSignal.timeout(PROBE_TIMEOUT_MS);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  try {
    const response = await fetch(`${config.apiUrl}${path}`, {
      headers: { Accept: 'application/json' },
      signal: combined,
    });
    return {
      state: response.ok ? 'operational' : 'unavailable',
      httpStatus: response.status,
    };
  } catch (error) {
    if (signal?.aborted) {
      throw error;
    }
    return { state: 'unreachable' };
  }
}

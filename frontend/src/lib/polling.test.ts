import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api/client.ts';
import { shouldRetry } from './queryClient.ts';

async function loadPolling() {
  vi.resetModules();
  return (await import('./polling.ts')).polling;
}

describe('polling configuration', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('is switched off in the test environment', async () => {
    const polling = await loadPolling();
    expect(Object.values(polling).every((value) => value === false)).toBe(true);
  });

  it('uses sensible defaults when nothing is configured', async () => {
    for (const name of [
      'VITE_POLL_OVERVIEW_MS',
      'VITE_POLL_RESOURCES_MS',
      'VITE_POLL_INCIDENTS_MS',
      'VITE_POLL_RULES_MS',
      'VITE_POLL_CHARTS_MS',
      'VITE_POLL_HISTORY_MS',
      'VITE_POLL_SYSTEM_MS',
    ]) {
      vi.stubEnv(name, '');
    }
    const polling = await loadPolling();
    expect(polling.overview).toBe(15_000);
    expect(polling.incidents).toBe(20_000);
    expect(polling.charts).toBe(60_000);
    // Charts change slowest, so they poll least often.
    expect(polling.charts).toBeGreaterThan(Number(polling.overview));
  });

  it('accepts overrides and treats zero or garbage as disabled', async () => {
    vi.stubEnv('VITE_POLL_OVERVIEW_MS', '5000');
    vi.stubEnv('VITE_POLL_CHARTS_MS', '0');
    vi.stubEnv('VITE_POLL_HISTORY_MS', 'soon');
    const polling = await loadPolling();
    expect(polling.overview).toBe(5000);
    expect(polling.charts).toBe(false);
    expect(polling.history).toBe(false);
  });
});

describe('retry policy', () => {
  it('retries transient failures a bounded number of times', () => {
    for (const kind of ['network', 'timeout', 'unavailable', 'server'] as const) {
      const error = new ApiError(kind, 'x');
      expect(shouldRetry(0, error)).toBe(true);
      expect(shouldRetry(1, error)).toBe(true);
      expect(shouldRetry(2, error)).toBe(false);
    }
  });

  it('never retries failures that will not fix themselves', () => {
    for (const kind of ['not_found', 'conflict', 'validation', 'http'] as const) {
      expect(shouldRetry(0, new ApiError(kind, 'x'))).toBe(false);
    }
    expect(shouldRetry(0, new Error('bug'))).toBe(false);
  });
});

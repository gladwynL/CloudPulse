import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, buildUrl, errorMessage, isApiError, request } from './client.ts';
import { api } from './endpoints.ts';
import { installMockApi, type MockApi } from '../../test/mockApi.ts';

let mock: MockApi;

async function failureOf(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error('expected the request to fail');
}

describe('buildUrl', () => {
  it('prefixes the configured API origin and drops empty query values', () => {
    const url = new URL(
      buildUrl('/api/v1/resources', { q: 'api', limit: 25, status: '', x: null }),
    );
    expect(url.origin).toBe('http://api.test');
    expect(url.pathname).toBe('/api/v1/resources');
    expect(url.searchParams.get('q')).toBe('api');
    expect(url.searchParams.get('limit')).toBe('25');
    expect(url.searchParams.has('status')).toBe(false);
    expect(url.searchParams.has('x')).toBe(false);
  });

  it('encodes values rather than interpolating them', () => {
    const url = new URL(buildUrl('/api/v1/resources', { q: 'a&b=c' }));
    expect(url.searchParams.get('q')).toBe('a&b=c');
    expect([...url.searchParams.keys()]).toEqual(['q']);
  });
});

describe('API origin configuration', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  async function loadBuildUrl() {
    vi.resetModules();
    return (await import('./client.ts')).buildUrl;
  }

  it('resolves "same-origin" against the page, so no direct API port is needed', async () => {
    vi.stubEnv('VITE_API_URL', 'same-origin');
    const url = new URL((await loadBuildUrl())('/api/v1/overview'));
    expect(url.origin).toBe(window.location.origin);
    expect(url.pathname).toBe('/api/v1/overview');
  });

  it('defaults to the local API and ignores trailing slashes', async () => {
    vi.stubEnv('VITE_API_URL', '');
    expect(new URL((await loadBuildUrl())('/health')).origin).toBe('http://localhost:8000');
    vi.stubEnv('VITE_API_URL', 'https://api.example.test///');
    expect((await loadBuildUrl())('/health')).toBe('https://api.example.test/health');
  });
});

describe('request', () => {
  afterEach(() => {
    mock = undefined as unknown as MockApi;
  });

  it('returns parsed JSON for a 2xx response', async () => {
    mock = installMockApi().get('/health', { status: 'ok' });
    await expect(api.health()).resolves.toEqual({ status: 'ok' });
  });

  it('sends a JSON body with a content type only when there is a body', async () => {
    mock = installMockApi().on('POST', '/api/v1/resources', ({ body }) => ({
      status: 201,
      body: { echoed: body },
    }));
    const result = await request<{ echoed: unknown }>('POST', '/api/v1/resources', {
      body: { name: 'x' },
    });
    expect(result.echoed).toEqual({ name: 'x' });
  });

  it('resolves a 204 to undefined', async () => {
    mock = installMockApi().on('DELETE', '/api/v1/resources/abc', { status: 204 });
    await expect(api.deleteResource('abc')).resolves.toBeUndefined();
  });

  it('maps 404 to not_found using the server message', async () => {
    mock = installMockApi().on('GET', '/api/v1/resources/missing', {
      status: 404,
      body: { detail: 'Resource not found' },
    });
    const error = await failureOf(api.getResource('missing'));
    expect(error.kind).toBe('not_found');
    expect(error.status).toBe(404);
    expect(error.message).toBe('Resource not found');
  });

  it('maps 409 to conflict', async () => {
    mock = installMockApi().on('POST', '/api/v1/incidents/i1/acknowledge', {
      status: 409,
      body: { detail: 'Incident is already resolved' },
    });
    const error = await failureOf(api.acknowledgeIncident('i1'));
    expect(error.kind).toBe('conflict');
    expect(error.message).toBe('Incident is already resolved');
  });

  it('maps 422 to validation with per-field messages', async () => {
    mock = installMockApi().on('POST', '/api/v1/resources', {
      status: 422,
      body: {
        detail: [
          { loc: ['body', 'url'], msg: 'URL host is not allowed', type: 'value_error' },
          { loc: ['body', 'name'], msg: 'String should have at least 1 character' },
          { loc: ['body', 'url'], msg: 'ignored second message for the same field' },
        ],
      },
    });
    const error = await failureOf(api.createResource({ name: '', url: 'http://x' }));
    expect(error.kind).toBe('validation');
    expect(error.fieldErrors).toEqual({
      url: 'URL host is not allowed',
      name: 'String should have at least 1 character',
    });
    expect(error.message).toBe('URL host is not allowed');
  });

  it.each([502, 503, 504])('maps %i to unavailable', async (status) => {
    mock = installMockApi().get('/api/v1/overview', { detail: 'down' }, status);
    expect((await failureOf(api.overview())).kind).toBe('unavailable');
  });

  it('maps other 5xx to server and unknown 4xx to http', async () => {
    mock = installMockApi()
      .get('/api/v1/overview', { detail: 'boom' }, 500)
      .get('/health', { detail: 'teapot' }, 418);
    expect((await failureOf(api.overview())).kind).toBe('server');
    expect((await failureOf(api.health())).kind).toBe('http');
  });

  it('uses a readable fallback when the error body is not JSON', async () => {
    mock = installMockApi().on('GET', '/api/v1/overview', { status: 503, body: undefined });
    const error = await failureOf(api.overview());
    expect(error.kind).toBe('unavailable');
    expect(error.message).toMatch(/unavailable/i);
  });

  it('reports a network failure without leaking the request URL', async () => {
    mock = installMockApi().on('GET', '/api/v1/resources', 'network-error');
    const error = await failureOf(
      request('GET', '/api/v1/resources', { query: { q: 'token=SECRET' } }),
    );
    expect(error.kind).toBe('network');
    expect(error.message).not.toContain('SECRET');
    expect(error.message).not.toContain('api.test');
  });

  it('reports a timeout when the API does not answer in time', async () => {
    mock = installMockApi();
    mock.on('GET', '/slow', () => new Promise(() => undefined));
    // The mock never replies, so make fetch honour the abort signal like a real one.
    globalThis.fetch = ((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(new DOMException('x', 'TimeoutError')),
        );
      })) as typeof fetch;
    const error = await failureOf(request('GET', '/slow', { timeoutMs: 20 }));
    expect(error.kind).toBe('timeout');
  });

  it('rethrows a caller cancellation instead of turning it into an API error', async () => {
    mock = installMockApi().get('/health', { status: 'ok' });
    const controller = new AbortController();
    controller.abort();
    const outcome = await request('GET', '/health', { signal: controller.signal }).catch(
      (e: unknown) => e,
    );
    expect(outcome).not.toBeInstanceOf(ApiError);
    expect((outcome as Error).name).toBe('AbortError');
  });
});

describe('error helpers', () => {
  it('errorMessage handles ApiError, Error and unknown values', () => {
    expect(errorMessage(new ApiError('conflict', 'nope', 409))).toBe('nope');
    expect(errorMessage(new Error('plain'))).toBe('plain');
    expect(errorMessage('weird')).toBe('Something went wrong.');
  });

  it('isApiError narrows by kind', () => {
    const error = new ApiError('not_found', 'x', 404);
    expect(isApiError(error)).toBe(true);
    expect(isApiError(error, 'not_found')).toBe(true);
    expect(isApiError(error, 'conflict')).toBe(false);
    expect(isApiError(new Error('x'))).toBe(false);
  });
});

describe('endpoint paths and parameters', () => {
  it('sends list filters as query parameters and omits unset ones', async () => {
    mock = installMockApi().get('/api/v1/resources', { items: [], total: 0, limit: 25, offset: 0 });
    await api.listResources({ q: 'api', is_enabled: false, limit: 25 });
    const [call] = mock.calls;
    expect(call?.query.get('q')).toBe('api');
    expect(call?.query.get('is_enabled')).toBe('false');
    expect(call?.query.get('limit')).toBe('25');
    expect(call?.query.has('offset')).toBe(false);
  });

  it('requests the incident list with the active filter and cursor', async () => {
    mock = installMockApi().get('/api/v1/incidents', { items: [], limit: 25, next_cursor: null });
    await api.listIncidents({ active: true, cursor: 'abc' });
    expect(mock.calls[0]?.query.get('active')).toBe('true');
    expect(mock.calls[0]?.query.get('cursor')).toBe('abc');
  });
});

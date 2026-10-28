import { vi } from 'vitest';

export interface RecordedCall {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

export type MockReply = { status?: number; body?: unknown } | 'network-error';
type Handler = (call: RecordedCall) => MockReply | Promise<MockReply>;

interface Route {
  method: string;
  path: string | RegExp;
  handler: Handler;
}

/**
 * A minimal in-memory stand-in for the CloudPulse API. Tests register the routes they expect;
 * a request to anything else fails loudly so an unplanned call can never pass silently.
 */
export class MockApi {
  readonly calls: RecordedCall[] = [];
  private readonly routes: Route[] = [];

  /** Register (or replace) a route. The most recently added matching route wins. */
  on(method: string, path: string | RegExp, reply: MockReply | Handler): this {
    const handler: Handler = typeof reply === 'function' ? reply : () => reply;
    this.routes.unshift({ method, path, handler });
    return this;
  }

  get(path: string | RegExp, body: unknown, status = 200): this {
    return this.on('GET', path, { status, body });
  }

  install(): this {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      this.handle(input, init),
    );
    return this;
  }

  callsTo(method: string, path: string | RegExp): RecordedCall[] {
    return this.calls.filter((call) => call.method === method && matches(path, call.path));
  }

  private async handle(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const signal = init?.signal;
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');

    const url = new URL(typeof input === 'string' ? input : input.toString());
    const method = (init?.method ?? 'GET').toUpperCase();
    const call: RecordedCall = {
      method,
      path: url.pathname,
      query: url.searchParams,
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    };
    this.calls.push(call);

    const route = this.routes.find((r) => r.method === method && matches(r.path, url.pathname));
    if (!route) {
      return json(500, { detail: `No mock registered for ${method} ${url.pathname}` });
    }
    const reply = await route.handler(call);
    if (reply === 'network-error') throw new TypeError('Failed to fetch');
    return json(reply.status ?? 200, reply.body);
  }
}

const matches = (pattern: string | RegExp, path: string) =>
  typeof pattern === 'string' ? pattern === path : pattern.test(path);

function json(status: number, body: unknown): Response {
  if (status === 204 || body === undefined) return new Response(null, { status });
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function installMockApi(): MockApi {
  return new MockApi().install();
}

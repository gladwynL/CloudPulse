import { config } from '../../config.ts';

/** How a request failed, so the UI can say something accurate about it. */
export type ApiErrorKind =
  | 'network' // could not reach the API at all
  | 'timeout' // no answer within the client timeout
  | 'not_found' // 404
  | 'conflict' // 409: valid request, but not allowed in the current state
  | 'validation' // 422: the server rejected the input (see fieldErrors)
  | 'unavailable' // 502/503/504: the API or its database is down
  | 'server' // other 5xx
  | 'http'; // any other non-2xx

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  /** Field name (as sent in the request body/query) -> message, from a 422 response. */
  readonly fieldErrors: Readonly<Record<string, string>>;

  constructor(
    kind: ApiErrorKind,
    message: string,
    status: number | null = null,
    fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

export const REQUEST_TIMEOUT_MS = 15_000;

type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  query?: Record<string, QueryValue>;
  body?: unknown;
  /** Cancellation from the caller (e.g. TanStack Query aborts stale requests). */
  signal?: AbortSignal;
  timeoutMs?: number;
}

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  // With a same-origin API (empty apiUrl) the path resolves against the page's own origin.
  const url = new URL(`${config.apiUrl}${path}`, globalThis.location?.origin);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === '') continue;
    url.searchParams.set(key, String(value));
  }
  return url.toString();
}

interface ValidationDetail {
  loc?: unknown[];
  msg?: string;
}

/**
 * Turn a FastAPI error body into a message and per-field errors. Only server-provided text is
 * used; request URLs (which can carry query strings) never appear in error messages.
 */
function parseErrorBody(body: unknown): { message: string | null; fields: Record<string, string> } {
  const fields: Record<string, string> = {};
  if (typeof body !== 'object' || body === null || !('detail' in body)) {
    return { message: null, fields };
  }
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === 'string') return { message: detail, fields };
  if (Array.isArray(detail)) {
    let first: string | null = null;
    for (const entry of detail as ValidationDetail[]) {
      const msg = entry.msg ?? 'Invalid value';
      first ??= msg;
      // loc is ["body" | "query" | "path", field, ...]; keep the first field segment.
      const field = (entry.loc ?? []).find((part, index) => index > 0 && typeof part === 'string');
      if (typeof field === 'string' && !(field in fields)) fields[field] = msg;
    }
    return { message: first, fields };
  }
  return { message: null, fields };
}

function classify(status: number): ApiErrorKind {
  if (status === 404) return 'not_found';
  if (status === 409) return 'conflict';
  if (status === 422) return 'validation';
  if (status === 502 || status === 503 || status === 504) return 'unavailable';
  if (status >= 500) return 'server';
  return 'http';
}

const FALLBACK_MESSAGES: Record<ApiErrorKind, string> = {
  network: 'Could not reach the CloudPulse API. Check that the backend is running.',
  timeout: 'The API took too long to respond.',
  not_found: 'That item was not found. It may have been deleted.',
  conflict: 'That action is not allowed in the current state.',
  validation: 'The server rejected the submitted values.',
  unavailable: 'The API or its database is temporarily unavailable.',
  server: 'The server hit an unexpected error.',
  http: 'The request failed.',
};

export async function request<T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const timeout = AbortSignal.timeout(options.timeoutMs ?? REQUEST_TIMEOUT_MS);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers: {
        Accept: 'application/json',
        ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal,
    });
  } catch (error) {
    if (options.signal?.aborted) throw error; // a deliberate cancellation, not a failure
    if (timeout.aborted) throw new ApiError('timeout', FALLBACK_MESSAGES.timeout);
    throw new ApiError('network', FALLBACK_MESSAGES.network);
  }

  if (response.status === 204) return undefined as T;

  let body: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    const kind = classify(response.status);
    const { message, fields } = parseErrorBody(body);
    throw new ApiError(kind, message ?? FALLBACK_MESSAGES[kind], response.status, fields);
  }
  return body as T;
}

/** The text to show a user for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return 'Something went wrong.';
}

export function isApiError(error: unknown, kind?: ApiErrorKind): error is ApiError {
  return error instanceof ApiError && (kind === undefined || error.kind === kind);
}

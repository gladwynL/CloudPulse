import { request } from './client.ts';
import type {
  AlertRule,
  AlertRuleCreate,
  AlertRuleListItem,
  AlertRuleListParams,
  AlertRuleUpdate,
  CheckHistory,
  CheckHistoryParams,
  HealthResponse,
  Incident,
  IncidentList,
  IncidentListParams,
  IncidentTimeline,
  MetricsSummary,
  Overview,
  Page,
  ReadinessResponse,
  Resource,
  ResourceCreate,
  ResourceListParams,
  ResourceStatusView,
  ResourceUpdate,
  SeriesInterval,
  Timeseries,
} from './types.ts';

/** Every function maps to one real backend route (see docs/architecture.md). */
type Opts = { signal?: AbortSignal };

const R = '/api/v1/resources';

export const api = {
  // --- overview and probes -------------------------------------------------------------
  overview: ({ signal }: Opts = {}) => request<Overview>('GET', '/api/v1/overview', { signal }),
  health: ({ signal }: Opts = {}) => request<HealthResponse>('GET', '/health', { signal }),
  /** `/ready` answers 503 with a body when the database is down; that surfaces as an ApiError. */
  ready: ({ signal }: Opts = {}) => request<ReadinessResponse>('GET', '/ready', { signal }),

  // --- resources ------------------------------------------------------------------------
  listResources: (params: ResourceListParams = {}, { signal }: Opts = {}) =>
    request<Page<Resource>>('GET', R, { query: { ...params }, signal }),
  getResource: (id: string, { signal }: Opts = {}) =>
    request<Resource>('GET', `${R}/${id}`, { signal }),
  createResource: (body: ResourceCreate) => request<Resource>('POST', R, { body }),
  updateResource: (id: string, body: ResourceUpdate) =>
    request<Resource>('PATCH', `${R}/${id}`, { body }),
  deleteResource: (id: string) => request<void>('DELETE', `${R}/${id}`),
  checkResource: (id: string) =>
    request<ResourceStatusView>('POST', `${R}/${id}/check`, {
      // A manual check runs the HTTP request inside the API call; allow for the resource timeout.
      timeoutMs: 45_000,
    }),

  // --- history and metrics ---------------------------------------------------------------
  listChecks: (id: string, params: CheckHistoryParams = {}, { signal }: Opts = {}) =>
    request<CheckHistory>('GET', `${R}/${id}/checks`, { query: { ...params }, signal }),
  metricsSummary: (id: string, window: { start: string; end: string }, { signal }: Opts = {}) =>
    request<MetricsSummary>('GET', `${R}/${id}/metrics/summary`, { query: window, signal }),
  metricsTimeseries: (
    id: string,
    window: { start: string; end: string },
    interval: SeriesInterval | 'auto' = 'auto',
    { signal }: Opts = {},
  ) =>
    request<Timeseries>('GET', `${R}/${id}/metrics/timeseries`, {
      query: { ...window, interval },
      signal,
    }),

  // --- alert rules -------------------------------------------------------------------------
  listAllRules: (params: AlertRuleListParams = {}, { signal }: Opts = {}) =>
    request<Page<AlertRuleListItem>>('GET', '/api/v1/alert-rules', {
      query: { ...params },
      signal,
    }),
  listResourceRules: (
    resourceId: string,
    params: { limit?: number; offset?: number } = {},
    { signal }: Opts = {},
  ) =>
    request<Page<AlertRule>>('GET', `${R}/${resourceId}/alert-rules`, {
      query: { ...params },
      signal,
    }),
  createRule: (resourceId: string, body: AlertRuleCreate) =>
    request<AlertRule>('POST', `${R}/${resourceId}/alert-rules`, { body }),
  updateRule: (id: string, body: AlertRuleUpdate) =>
    request<AlertRule>('PATCH', `/api/v1/alert-rules/${id}`, { body }),
  deleteRule: (id: string) => request<void>('DELETE', `/api/v1/alert-rules/${id}`),

  // --- incidents ---------------------------------------------------------------------------
  listIncidents: (params: IncidentListParams = {}, { signal }: Opts = {}) =>
    request<IncidentList>('GET', '/api/v1/incidents', { query: { ...params }, signal }),
  getIncident: (id: string, { signal }: Opts = {}) =>
    request<Incident>('GET', `/api/v1/incidents/${id}`, { signal }),
  acknowledgeIncident: (id: string) =>
    request<Incident>('POST', `/api/v1/incidents/${id}/acknowledge`),
  incidentEvents: (id: string, { signal }: Opts = {}) =>
    request<IncidentTimeline>('GET', `/api/v1/incidents/${id}/events`, { signal }),
};

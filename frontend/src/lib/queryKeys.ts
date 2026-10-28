/**
 * Query keys. Per-resource data lives under ['resource', id, ...] so invalidating
 * ['resource', id] refreshes its detail, summary, series, history and rules together.
 */
export const keys = {
  overview: ['overview'] as const,
  system: ['system'] as const,
  resources: ['resources'] as const,
  resourceList: (params: object) => ['resources', 'list', params] as const,
  resource: (id: string) => ['resource', id] as const,
  resourceDetail: (id: string) => ['resource', id, 'detail'] as const,
  summary: (id: string, period: string) => ['resource', id, 'summary', period] as const,
  timeseries: (id: string, period: string) => ['resource', id, 'timeseries', period] as const,
  history: (id: string, period: string, status: string | undefined) =>
    ['resource', id, 'history', period, status ?? 'all'] as const,
  resourceRules: (id: string) => ['resource', id, 'rules'] as const,
  rules: ['alert-rules'] as const,
  ruleList: (params: object) => ['alert-rules', 'list', params] as const,
  incidents: ['incidents'] as const,
  incidentList: (params: object) => ['incidents', 'list', params] as const,
  incident: (id: string) => ['incident', id] as const,
  incidentDetail: (id: string) => ['incident', id, 'detail'] as const,
  incidentEvents: (id: string) => ['incident', id, 'events'] as const,
  triggeringCheck: (id: string) => ['incident', id, 'trigger'] as const,
};

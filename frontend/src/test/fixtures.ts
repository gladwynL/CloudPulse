import type {
  AlertRule,
  AlertRuleListItem,
  Incident,
  IncidentEvent,
  MetricsSummary,
  Overview,
  Resource,
  Timeseries,
} from '../lib/api/types.ts';

const NOW = '2026-10-28T12:00:00Z';

export function makeResource(overrides: Partial<Resource> = {}): Resource {
  return {
    id: 'res-1',
    name: 'Checkout API',
    description: null,
    url: 'https://checkout.example.com/health',
    is_enabled: true,
    check_interval_seconds: 60,
    timeout_seconds: 10,
    expected_status_code: 200,
    current_status: 'HEALTHY',
    last_checked_at: NOW,
    next_check_at: '2026-10-28T12:01:00Z',
    last_response_time_ms: 42.5,
    last_http_status_code: 200,
    last_error: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

export function makeRule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id: 'rule-1',
    resource_id: 'res-1',
    name: 'Three failures',
    description: null,
    is_enabled: true,
    rule_type: 'CONSECUTIVE_FAILURES',
    severity: 'WARNING',
    consecutive_failures: 3,
    operator: null,
    threshold: null,
    status_condition: null,
    recovery_successes: 1,
    current_state: 'OK',
    failure_streak: 0,
    recovery_streak: 0,
    last_evaluated_at: NOW,
    last_triggered_at: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

export function makeRuleListItem(overrides: Partial<AlertRuleListItem> = {}): AlertRuleListItem {
  return { ...makeRule(), resource_name: 'Checkout API', ...overrides };
}

export function makeIncident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: 'inc-1',
    resource_id: 'res-1',
    alert_rule_id: 'rule-1',
    resource_name: 'Checkout API',
    rule_name: 'Three failures',
    rule_type: 'CONSECUTIVE_FAILURES',
    title: 'WARNING: Three failures on Checkout API',
    severity: 'WARNING',
    status: 'OPEN',
    triggered_at: '2026-10-28T11:50:00Z',
    acknowledged_at: null,
    resolved_at: null,
    resolution_reason: null,
    last_observed_at: NOW,
    opened_by_check_id: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

export function makeEvent(overrides: Partial<IncidentEvent> = {}): IncidentEvent {
  return {
    id: 'evt-1',
    incident_id: 'inc-1',
    event_type: 'OPENED',
    occurred_at: '2026-10-28T11:50:00Z',
    actor: 'SYSTEM',
    details: {},
    ...overrides,
  };
}

export function makeOverview(overrides: Partial<Overview> = {}): Overview {
  return {
    resources: {
      total: 230,
      enabled: 228,
      disabled: 2,
      by_status: { UNKNOWN: 5, HEALTHY: 200, DEGRADED: 15, DOWN: 10 },
    },
    alert_rules: { total: 40, enabled: 38, firing: 3 },
    incidents: { open: 2, acknowledged: 1, active: 3 },
    limits: { max_rules_per_resource: 20 },
    ...overrides,
  };
}

export function makeSummary(overrides: Partial<MetricsSummary> = {}): MetricsSummary {
  return {
    resource_id: 'res-1',
    window: { start: '2026-10-27T12:00:00Z', end: NOW },
    checks: { total: 100, healthy: 90, degraded: 6, down: 4 },
    availability_percent: 90,
    latency: { samples: 96, avg_ms: 55, min_ms: 10, max_ms: 400, p50_ms: 40, p95_ms: 210 },
    first_checked_at: '2026-10-27T12:00:30Z',
    last_checked_at: NOW,
    ...overrides,
  };
}

export function makeTimeseries(overrides: Partial<Timeseries> = {}): Timeseries {
  return {
    resource_id: 'res-1',
    window: { start: '2026-10-28T11:00:00Z', end: NOW },
    interval: '5m',
    buckets: [],
    ...overrides,
  };
}

/**
 * Wire types mirroring the backend's Pydantic response/request schemas
 * (backend/app/schemas/*). Timestamps are ISO 8601 strings in UTC.
 */

export type ResourceStatus = 'UNKNOWN' | 'HEALTHY' | 'DEGRADED' | 'DOWN';
export type CheckStatus = 'HEALTHY' | 'DEGRADED' | 'DOWN';
export type CheckSource = 'SCHEDULED' | 'MANUAL';
export type ErrorCategory =
  | 'timeout'
  | 'dns'
  | 'connection'
  | 'tls'
  | 'blocked'
  | 'protocol'
  | 'unexpected_status'
  | 'internal';

export type AlertRuleType = 'CONSECUTIVE_FAILURES' | 'LATENCY_THRESHOLD' | 'STATUS';
export type AlertOperator = 'GREATER_THAN' | 'GREATER_THAN_OR_EQUAL';
export type StatusCondition = 'DOWN' | 'DEGRADED' | 'UNHEALTHY';
export type AlertSeverity = 'WARNING' | 'CRITICAL';
export type AlertState = 'INSUFFICIENT_DATA' | 'OK' | 'FIRING';
export type IncidentStatus = 'OPEN' | 'ACKNOWLEDGED' | 'RESOLVED';
export type ResolutionReason =
  | 'RECOVERED'
  | 'RULE_DISABLED'
  | 'RULE_DELETED'
  | 'RULE_RECONFIGURED'
  | 'RESOURCE_DISABLED'
  | 'RESOURCE_DELETED';
export type IncidentEventType = 'OPENED' | 'ACKNOWLEDGED' | ResolutionReason;
export type Actor = 'SYSTEM' | 'MANUAL';
export type SeriesInterval = '1m' | '5m' | '15m' | '1h' | '1d';

// --- Resources ----------------------------------------------------------------------------

export interface Resource {
  id: string;
  name: string;
  description: string | null;
  url: string;
  is_enabled: boolean;
  check_interval_seconds: number;
  timeout_seconds: number;
  expected_status_code: number;
  current_status: ResourceStatus;
  last_checked_at: string | null;
  next_check_at: string | null;
  last_response_time_ms: number | null;
  last_http_status_code: number | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResourceStatusView {
  id: string;
  name: string;
  is_enabled: boolean;
  current_status: ResourceStatus;
  last_checked_at: string | null;
  next_check_at: string | null;
  last_response_time_ms: number | null;
  last_http_status_code: number | null;
  last_error: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ResourceCreate {
  name: string;
  description?: string | null;
  url: string;
  is_enabled?: boolean;
  check_interval_seconds?: number | null;
  timeout_seconds?: number | null;
  expected_status_code?: number;
}

export type ResourceUpdate = Partial<{
  name: string;
  description: string | null;
  url: string;
  is_enabled: boolean;
  check_interval_seconds: number;
  timeout_seconds: number;
  expected_status_code: number;
}>;

export interface ResourceListParams {
  limit?: number;
  offset?: number;
  q?: string;
  current_status?: ResourceStatus;
  is_enabled?: boolean;
}

// --- Metrics ------------------------------------------------------------------------------

export interface TimeWindow {
  start: string;
  end: string;
}

export interface CheckResult {
  id: string;
  checked_at: string;
  status: CheckStatus;
  http_status_code: number | null;
  response_time_ms: number | null;
  duration_ms: number;
  error_category: ErrorCategory | null;
  error_summary: string | null;
  source: CheckSource;
  created_at: string;
}

export interface CheckHistory {
  resource_id: string;
  window: TimeWindow;
  items: CheckResult[];
  limit: number;
  next_cursor: string | null;
}

export interface CheckHistoryParams {
  start?: string;
  end?: string;
  status?: CheckStatus;
  limit?: number;
  cursor?: string;
}

export interface MetricsSummary {
  resource_id: string;
  window: TimeWindow;
  checks: { total: number; healthy: number; degraded: number; down: number };
  /** healthy / total * 100; null when there were no checks (unknown, not 0 or 100). */
  availability_percent: number | null;
  latency: {
    samples: number;
    avg_ms: number | null;
    min_ms: number | null;
    max_ms: number | null;
    p50_ms: number | null;
    p95_ms: number | null;
  };
  first_checked_at: string | null;
  last_checked_at: string | null;
}

export interface TimeseriesBucket {
  bucket_start: string;
  total: number;
  healthy: number;
  degraded: number;
  down: number;
  availability_percent: number | null;
  avg_response_time_ms: number | null;
  p95_response_time_ms: number | null;
}

export interface Timeseries {
  resource_id: string;
  window: TimeWindow;
  interval: SeriesInterval;
  /** Only buckets that contain at least one check; a missing bucket means no observations. */
  buckets: TimeseriesBucket[];
}

// --- Alert rules --------------------------------------------------------------------------

export interface AlertRule {
  id: string;
  resource_id: string;
  name: string;
  description: string | null;
  is_enabled: boolean;
  rule_type: AlertRuleType;
  severity: AlertSeverity;
  consecutive_failures: number | null;
  operator: AlertOperator | null;
  threshold: number | null;
  status_condition: StatusCondition | null;
  recovery_successes: number;
  current_state: AlertState;
  failure_streak: number;
  recovery_streak: number;
  last_evaluated_at: string | null;
  last_triggered_at: string | null;
  created_at: string;
  updated_at: string;
}

/** A rule in the cross-resource listing, with its resource's name. */
export interface AlertRuleListItem extends AlertRule {
  resource_name: string;
}

export interface AlertRuleCreate {
  name: string;
  description?: string | null;
  rule_type: AlertRuleType;
  is_enabled?: boolean;
  severity?: AlertSeverity;
  consecutive_failures?: number | null;
  operator?: AlertOperator | null;
  threshold?: number | null;
  status_condition?: StatusCondition | null;
  recovery_successes?: number;
}

export type AlertRuleUpdate = Partial<{
  name: string;
  description: string | null;
  is_enabled: boolean;
  severity: AlertSeverity;
  consecutive_failures: number | null;
  operator: AlertOperator | null;
  threshold: number | null;
  status_condition: StatusCondition | null;
  recovery_successes: number;
}>;

export interface AlertRuleListParams {
  resource_id?: string;
  current_state?: AlertState;
  severity?: AlertSeverity;
  is_enabled?: boolean;
  limit?: number;
  offset?: number;
}

// --- Incidents ----------------------------------------------------------------------------

export interface Incident {
  id: string;
  /** Null once the resource has been deleted (the name snapshot remains). */
  resource_id: string | null;
  /** Null once the rule has been deleted (the name snapshot remains). */
  alert_rule_id: string | null;
  resource_name: string;
  rule_name: string;
  rule_type: AlertRuleType;
  title: string;
  severity: AlertSeverity;
  status: IncidentStatus;
  triggered_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  resolution_reason: ResolutionReason | null;
  last_observed_at: string;
  opened_by_check_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface IncidentList {
  items: Incident[];
  limit: number;
  next_cursor: string | null;
}

export interface IncidentListParams {
  status?: IncidentStatus;
  active?: boolean;
  severity?: AlertSeverity;
  resource_id?: string;
  start?: string;
  end?: string;
  limit?: number;
  cursor?: string;
}

export interface IncidentEvent {
  id: string;
  incident_id: string;
  event_type: IncidentEventType;
  occurred_at: string;
  actor: Actor;
  details: Record<string, unknown>;
}

export interface IncidentTimeline {
  incident_id: string;
  items: IncidentEvent[];
}

// --- Overview and probes ------------------------------------------------------------------

export interface Overview {
  resources: {
    total: number;
    enabled: number;
    disabled: number;
    by_status: Record<ResourceStatus, number>;
  };
  alert_rules: { total: number; enabled: number; firing: number };
  incidents: { open: number; acknowledged: number; active: number };
  limits: { max_rules_per_resource: number };
}

export interface HealthResponse {
  status: 'ok';
}

export interface ReadinessResponse {
  status: 'ready' | 'unavailable';
  checks: { database: 'ok' | 'unavailable' };
}

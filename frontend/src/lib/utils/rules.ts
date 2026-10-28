import type {
  AlertOperator,
  AlertRule,
  AlertRuleType,
  ResolutionReason,
  StatusCondition,
} from '../api/types.ts';

export const RULE_TYPE_LABELS: Record<AlertRuleType, string> = {
  CONSECUTIVE_FAILURES: 'Consecutive failures',
  STATUS: 'Immediate status',
  LATENCY_THRESHOLD: 'Latency threshold',
};

export const RULE_TYPE_HINTS: Record<AlertRuleType, string> = {
  CONSECUTIVE_FAILURES:
    'Fires after several unhealthy checks in a row (degraded or down), so one blip never opens an incident.',
  STATUS:
    'Fires on a single check with the chosen status. Use it for "tell me the moment it is down".',
  LATENCY_THRESHOLD:
    'Fires when response time stays over a threshold for several checks in a row. Checks with no response are ignored, not counted.',
};

export const STATUS_CONDITION_LABELS: Record<StatusCondition, string> = {
  DOWN: 'Down',
  DEGRADED: 'Degraded',
  UNHEALTHY: 'Degraded or down',
};

export const OPERATOR_SYMBOLS: Record<AlertOperator, string> = {
  GREATER_THAN: '>',
  GREATER_THAN_OR_EQUAL: '≥',
};

export const OPERATOR_LABELS: Record<AlertOperator, string> = {
  GREATER_THAN: 'Greater than (>)',
  GREATER_THAN_OR_EQUAL: 'Greater than or equal (≥)',
};

export const RESOLUTION_LABELS: Record<ResolutionReason, string> = {
  RECOVERED: 'Recovered automatically',
  RULE_DISABLED: 'Rule was disabled',
  RULE_DELETED: 'Rule was deleted',
  RULE_RECONFIGURED: 'Rule was reconfigured',
  RESOURCE_DISABLED: 'Monitoring of the resource was disabled',
  RESOURCE_DELETED: 'Resource was deleted',
};

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

export type RuleShape = Pick<
  AlertRule,
  | 'rule_type'
  | 'consecutive_failures'
  | 'operator'
  | 'threshold'
  | 'status_condition'
  | 'recovery_successes'
>;

/** One phrase describing when the rule fires. */
export function describeTrigger(rule: RuleShape): string {
  switch (rule.rule_type) {
    case 'CONSECUTIVE_FAILURES':
      return plural(rule.consecutive_failures ?? 0, 'consecutive unhealthy check');
    case 'STATUS': {
      const label = rule.status_condition
        ? STATUS_CONDITION_LABELS[rule.status_condition].toLowerCase()
        : '?';
      return `Immediately when status is ${label}`;
    }
    case 'LATENCY_THRESHOLD': {
      const symbol = rule.operator ? OPERATOR_SYMBOLS[rule.operator] : '?';
      const count = plural(rule.consecutive_failures ?? 0, 'consecutive check');
      return `Response time ${symbol} ${rule.threshold ?? '?'} ms for ${count}`;
    }
  }
}

/** One phrase describing when the rule recovers. */
export function describeRecovery(rule: RuleShape): string {
  const n = rule.recovery_successes;
  return rule.rule_type === 'LATENCY_THRESHOLD'
    ? `Recovers after ${plural(n, 'check')} within the threshold`
    : `Recovers after ${plural(n, 'healthy check')}`;
}

/** Fields whose change closes a firing rule's incident (see the backend's alert semantics). */
export const MATERIAL_FIELDS = [
  'consecutive_failures',
  'operator',
  'threshold',
  'status_condition',
  'recovery_successes',
] as const;

/** There is no authentication yet, so a MANUAL actor is "someone using the API", not a person. */
export function actorLabel(actor: 'SYSTEM' | 'MANUAL'): string {
  return actor === 'SYSTEM' ? 'System' : 'Manual (API call, no verified identity)';
}

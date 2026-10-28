import type {
  AlertOperator,
  AlertRule,
  AlertRuleCreate,
  AlertRuleType,
  AlertRuleUpdate,
  AlertSeverity,
  StatusCondition,
} from '../../lib/api/types.ts';
import { MATERIAL_FIELDS } from '../../lib/utils/rules.ts';

export interface RuleFormValues {
  name: string;
  description: string;
  ruleType: AlertRuleType;
  severity: AlertSeverity;
  consecutiveFailures: string;
  operator: AlertOperator;
  threshold: string;
  statusCondition: StatusCondition;
  recoverySuccesses: string;
  enabled: boolean;
}

export type RuleFormField =
  | 'name'
  | 'description'
  | 'consecutiveFailures'
  | 'threshold'
  | 'recoverySuccesses'
  | 'operator'
  | 'statusCondition';
export type RuleFormErrors = Partial<Record<RuleFormField, string>>;

export const DEFAULT_RULE_VALUES: RuleFormValues = {
  name: '',
  description: '',
  ruleType: 'CONSECUTIVE_FAILURES',
  severity: 'WARNING',
  consecutiveFailures: '3',
  operator: 'GREATER_THAN',
  threshold: '1000',
  statusCondition: 'DOWN',
  recoverySuccesses: '1',
  enabled: true,
};

export function valuesFromRule(rule: AlertRule): RuleFormValues {
  return {
    name: rule.name,
    description: rule.description ?? '',
    ruleType: rule.rule_type,
    severity: rule.severity,
    consecutiveFailures: String(
      rule.consecutive_failures ?? DEFAULT_RULE_VALUES.consecutiveFailures,
    ),
    operator: rule.operator ?? DEFAULT_RULE_VALUES.operator,
    threshold: rule.threshold !== null ? String(rule.threshold) : DEFAULT_RULE_VALUES.threshold,
    statusCondition: rule.status_condition ?? DEFAULT_RULE_VALUES.statusCondition,
    recoverySuccesses: String(rule.recovery_successes),
    enabled: rule.is_enabled,
  };
}

/** Which inputs each rule type shows. Irrelevant inputs are never rendered or sent. */
export function fieldsFor(ruleType: AlertRuleType) {
  return {
    consecutive: ruleType === 'CONSECUTIVE_FAILURES' || ruleType === 'LATENCY_THRESHOLD',
    latency: ruleType === 'LATENCY_THRESHOLD',
    status: ruleType === 'STATUS',
  };
}

/** Maps backend field names (from a 422) to this form's fields. */
export const RULE_SERVER_FIELD_MAP: Record<string, RuleFormField> = {
  name: 'name',
  description: 'description',
  consecutive_failures: 'consecutiveFailures',
  operator: 'operator',
  threshold: 'threshold',
  status_condition: 'statusCondition',
  recovery_successes: 'recoverySuccesses',
};

const whole = (text: string) => /^\d+$/.test(text.trim());

export function validateRuleForm(values: RuleFormValues): RuleFormErrors {
  const errors: RuleFormErrors = {};
  const name = values.name.trim();
  if (!name) errors.name = 'Enter a name.';
  else if (name.length > 100) errors.name = 'Use 100 characters or fewer.';
  if (values.description.length > 500) errors.description = 'Use 500 characters or fewer.';

  const show = fieldsFor(values.ruleType);
  if (show.consecutive) {
    const n = Number(values.consecutiveFailures);
    if (!whole(values.consecutiveFailures) || n < 1 || n > 20) {
      errors.consecutiveFailures = 'Enter a whole number from 1 to 20.';
    }
  }
  if (show.latency) {
    const t = Number(values.threshold);
    if (values.threshold.trim() === '' || !Number.isFinite(t) || t <= 0 || t > 600_000) {
      errors.threshold = 'Enter a threshold above 0 and up to 600000 ms.';
    } else if (!/^\d+(\.\d{1,2})?$/.test(values.threshold.trim())) {
      errors.threshold = 'Use at most two decimal places.';
    }
  }
  const r = Number(values.recoverySuccesses);
  if (!whole(values.recoverySuccesses) || r < 1 || r > 20) {
    errors.recoverySuccesses = 'Enter a whole number from 1 to 20.';
  }
  return errors;
}

export function buildRuleCreate(values: RuleFormValues): AlertRuleCreate {
  const show = fieldsFor(values.ruleType);
  return {
    name: values.name.trim(),
    description: values.description.trim() || null,
    rule_type: values.ruleType,
    severity: values.severity,
    is_enabled: values.enabled,
    recovery_successes: Number(values.recoverySuccesses),
    ...(show.consecutive ? { consecutive_failures: Number(values.consecutiveFailures) } : {}),
    ...(show.latency ? { operator: values.operator, threshold: Number(values.threshold) } : {}),
    ...(show.status ? { status_condition: values.statusCondition } : {}),
  };
}

/** Only changed fields; type-specific fields only for this rule's type. */
export function buildRuleUpdate(values: RuleFormValues, rule: AlertRule): AlertRuleUpdate {
  const next = buildRuleCreate(values);
  const update: AlertRuleUpdate = {};
  if (next.name !== rule.name) update.name = next.name;
  if ((next.description ?? null) !== rule.description)
    update.description = next.description ?? null;
  if (next.severity !== rule.severity) update.severity = next.severity;
  if (next.is_enabled !== rule.is_enabled) update.is_enabled = next.is_enabled;
  if (next.recovery_successes !== rule.recovery_successes) {
    update.recovery_successes = next.recovery_successes;
  }
  if (
    next.consecutive_failures !== undefined &&
    next.consecutive_failures !== rule.consecutive_failures
  ) {
    update.consecutive_failures = next.consecutive_failures;
  }
  if (next.operator !== undefined && next.operator !== rule.operator)
    update.operator = next.operator;
  if (next.threshold !== undefined && Number(next.threshold) !== Number(rule.threshold)) {
    update.threshold = next.threshold;
  }
  if (next.status_condition !== undefined && next.status_condition !== rule.status_condition) {
    update.status_condition = next.status_condition;
  }
  return update;
}

/** Disabling or changing a trigger/recovery setting resolves a firing rule's active incident. */
export function resolvesActiveIncident(update: AlertRuleUpdate, rule: AlertRule): boolean {
  if (rule.current_state !== 'FIRING') return false;
  if (update.is_enabled === false) return true;
  return MATERIAL_FIELDS.some((field) => update[field] !== undefined);
}

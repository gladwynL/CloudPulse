import { describe, expect, it } from 'vitest';
import { makeRule } from '../../test/fixtures.ts';
import {
  buildRuleCreate,
  buildRuleUpdate,
  DEFAULT_RULE_VALUES,
  fieldsFor,
  resolvesActiveIncident,
  validateRuleForm,
  valuesFromRule,
  type RuleFormValues,
} from './form.ts';

const base: RuleFormValues = { ...DEFAULT_RULE_VALUES, name: 'Rule' };

describe('fieldsFor', () => {
  it('shows only the inputs a rule type uses', () => {
    expect(fieldsFor('CONSECUTIVE_FAILURES')).toEqual({
      consecutive: true,
      latency: false,
      status: false,
    });
    expect(fieldsFor('STATUS')).toEqual({ consecutive: false, latency: false, status: true });
    expect(fieldsFor('LATENCY_THRESHOLD')).toEqual({
      consecutive: true,
      latency: true,
      status: false,
    });
  });
});

describe('validateRuleForm', () => {
  it('accepts the defaults once named', () => {
    expect(validateRuleForm(base)).toEqual({});
  });

  it('requires a name', () => {
    expect(validateRuleForm({ ...base, name: '' }).name).toBeDefined();
  });

  it('bounds the failure count to 1-20', () => {
    expect(
      validateRuleForm({ ...base, consecutiveFailures: '0' }).consecutiveFailures,
    ).toBeDefined();
    expect(
      validateRuleForm({ ...base, consecutiveFailures: '21' }).consecutiveFailures,
    ).toBeDefined();
    expect(
      validateRuleForm({ ...base, consecutiveFailures: '20' }).consecutiveFailures,
    ).toBeUndefined();
  });

  it('ignores inputs the chosen type does not use', () => {
    const errors = validateRuleForm({
      ...base,
      ruleType: 'STATUS',
      consecutiveFailures: 'junk',
      threshold: 'junk',
    });
    expect(errors).toEqual({});
  });

  it('validates the latency threshold only for latency rules', () => {
    const latency = { ...base, ruleType: 'LATENCY_THRESHOLD' as const };
    expect(validateRuleForm({ ...latency, threshold: '' }).threshold).toBeDefined();
    expect(validateRuleForm({ ...latency, threshold: '0' }).threshold).toBeDefined();
    expect(validateRuleForm({ ...latency, threshold: '600001' }).threshold).toBeDefined();
    expect(validateRuleForm({ ...latency, threshold: '10.123' }).threshold).toMatch(/decimal/);
    expect(validateRuleForm({ ...latency, threshold: '250.5' }).threshold).toBeUndefined();
  });

  it('bounds recovery successes', () => {
    expect(validateRuleForm({ ...base, recoverySuccesses: '0' }).recoverySuccesses).toBeDefined();
    expect(validateRuleForm({ ...base, recoverySuccesses: '5' }).recoverySuccesses).toBeUndefined();
  });
});

describe('buildRuleCreate', () => {
  it('sends only the fields for a consecutive-failure rule', () => {
    const body = buildRuleCreate({ ...base, consecutiveFailures: '4', recoverySuccesses: '2' });
    expect(body).toEqual({
      name: 'Rule',
      description: null,
      rule_type: 'CONSECUTIVE_FAILURES',
      severity: 'WARNING',
      is_enabled: true,
      recovery_successes: 2,
      consecutive_failures: 4,
    });
    expect(body).not.toHaveProperty('threshold');
    expect(body).not.toHaveProperty('status_condition');
  });

  it('sends the status condition for a status rule', () => {
    const body = buildRuleCreate({ ...base, ruleType: 'STATUS', statusCondition: 'UNHEALTHY' });
    expect(body.status_condition).toBe('UNHEALTHY');
    expect(body).not.toHaveProperty('consecutive_failures');
  });

  it('sends operator, threshold and streak for a latency rule', () => {
    const body = buildRuleCreate({
      ...base,
      ruleType: 'LATENCY_THRESHOLD',
      operator: 'GREATER_THAN_OR_EQUAL',
      threshold: '750',
      consecutiveFailures: '2',
    });
    expect(body).toMatchObject({
      operator: 'GREATER_THAN_OR_EQUAL',
      threshold: 750,
      consecutive_failures: 2,
    });
  });
});

describe('buildRuleUpdate and incident resolution', () => {
  it('sends nothing for an unchanged edit', () => {
    const rule = makeRule();
    expect(buildRuleUpdate(valuesFromRule(rule), rule)).toEqual({});
  });

  it('sends only changed fields', () => {
    const rule = makeRule();
    const values = {
      ...valuesFromRule(rule),
      severity: 'CRITICAL' as const,
      consecutiveFailures: '5',
    };
    expect(buildRuleUpdate(values, rule)).toEqual({
      severity: 'CRITICAL',
      consecutive_failures: 5,
    });
  });

  it('compares numeric thresholds by value, not text', () => {
    const rule = makeRule({
      rule_type: 'LATENCY_THRESHOLD',
      operator: 'GREATER_THAN',
      threshold: 500,
      consecutive_failures: 3,
    });
    const values = { ...valuesFromRule(rule), threshold: '500.00' };
    expect(buildRuleUpdate(values, rule)).toEqual({});
  });

  it('warns only when a firing rule is disabled or materially reconfigured', () => {
    const firing = makeRule({ current_state: 'FIRING' });
    expect(resolvesActiveIncident({ is_enabled: false }, firing)).toBe(true);
    expect(resolvesActiveIncident({ consecutive_failures: 5 }, firing)).toBe(true);
    expect(resolvesActiveIncident({ recovery_successes: 3 }, firing)).toBe(true);
    // Renaming or changing severity does not close the incident.
    expect(resolvesActiveIncident({ name: 'x', severity: 'CRITICAL' }, firing)).toBe(false);
    // A rule that is not firing has nothing to resolve.
    expect(resolvesActiveIncident({ is_enabled: false }, makeRule({ current_state: 'OK' }))).toBe(
      false,
    );
  });
});

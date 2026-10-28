import { describe, expect, it } from 'vitest';
import {
  displayTarget,
  formatCount,
  formatDateTime,
  formatDuration,
  formatInterval,
  formatMs,
  formatPercent,
  formatRelative,
  utcTitle,
} from './format.ts';
import { PERIODS, DEFAULT_PERIOD, parsePeriod, windowFor } from './period.ts';
import {
  describeRecovery,
  describeTrigger,
  actorLabel,
  RESOLUTION_LABELS,
  MATERIAL_FIELDS,
} from './rules.ts';
import { STATUS_ORDER } from './status.ts';

describe('formatMs', () => {
  it.each([
    [null, '—'],
    [undefined, '—'],
    [3.84, '3.8 ms'],
    [250.4, '250 ms'],
    [1500, '1.50 s'],
  ])('formats %s', (input, expected) => {
    expect(formatMs(input)).toBe(expected);
  });
});

describe('formatPercent', () => {
  it('trims trailing zeros and keeps unknown distinct from zero', () => {
    expect(formatPercent(100)).toBe('100%');
    expect(formatPercent(99.5)).toBe('99.5%');
    expect(formatPercent(62.456)).toBe('62.46%');
    expect(formatPercent(0)).toBe('0%');
    expect(formatPercent(null)).toBe('—');
  });
});

describe('formatCount and formatInterval', () => {
  it('groups thousands', () => {
    expect(formatCount(1234567)).toBe('1,234,567');
  });

  it.each([
    [5, '5s'],
    [60, '1 min'],
    [90, '90s'],
    [3600, '1 h'],
    [5400, '90 min'],
  ])('formats a %is interval', (seconds, expected) => {
    expect(formatInterval(seconds)).toBe(expected);
  });
});

describe('date helpers', () => {
  it('shows a dash for missing or invalid dates', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime('not a date')).toBe('—');
    expect(utcTitle(null)).toBeUndefined();
    expect(utcTitle('nope')).toBeUndefined();
  });

  it('exposes the exact UTC instant', () => {
    expect(utcTitle('2026-10-28T12:00:00Z')).toBe('2026-10-28T12:00:00.000Z (UTC)');
    expect(formatDateTime('2026-10-28T12:00:00Z')).toMatch(/2026/);
  });

  it('formats relative times in both directions', () => {
    const now = Date.parse('2026-10-28T12:00:00Z');
    expect(formatRelative(null, now)).toBe('never');
    expect(formatRelative('2026-10-28T11:59:58Z', now)).toBe('just now');
    expect(formatRelative('2026-10-28T11:59:30Z', now)).toBe('30s ago');
    expect(formatRelative('2026-10-28T11:55:00Z', now)).toBe('5m ago');
    expect(formatRelative('2026-10-28T09:00:00Z', now)).toBe('3h ago');
    expect(formatRelative('2026-10-26T12:00:00Z', now)).toBe('2d ago');
    expect(formatRelative('2026-10-28T12:01:00Z', now)).toBe('in 1m');
  });

  it('formats durations', () => {
    expect(formatDuration('2026-10-28T12:00:00Z', '2026-10-28T12:00:09Z')).toBe('9s');
    expect(formatDuration('2026-10-28T12:00:00Z', '2026-10-28T12:04:12Z')).toBe('4m 12s');
    expect(formatDuration('2026-10-28T12:00:00Z', '2026-10-28T14:30:00Z')).toBe('2h 30m');
    expect(formatDuration('2026-10-28T12:00:00Z', '2026-10-30T15:00:00Z')).toBe('2d 3h');
    expect(formatDuration('2026-10-28T12:00:10Z', '2026-10-28T12:00:00Z')).toBe('0s');
  });
});

describe('displayTarget', () => {
  it('shows host and path only', () => {
    expect(displayTarget('https://api.example.com/v1/health')).toBe('api.example.com/v1/health');
    expect(displayTarget('http://demo-target:8080/')).toBe('demo-target:8080');
  });

  it('never shows query strings, fragments or credentials', () => {
    const shown = displayTarget('https://user:pass@example.com/x?token=SECRET#frag');
    expect(shown).toBe('example.com/x?…');
    expect(shown).not.toContain('SECRET');
    expect(shown).not.toContain('pass');
    expect(shown).not.toContain('frag');
  });

  it('does not throw on garbage', () => {
    expect(displayTarget('not a url')).toBe('invalid URL');
  });
});

describe('periods', () => {
  it('offers the four supported windows and defaults to 24h', () => {
    expect(PERIODS.map((p) => p.id)).toEqual(['1h', '24h', '7d', '30d']);
    expect(DEFAULT_PERIOD).toBe('24h');
  });

  it('falls back to the default for unknown values', () => {
    expect(parsePeriod('7d')).toBe('7d');
    expect(parsePeriod('90d')).toBe('24h');
    expect(parsePeriod(null)).toBe('24h');
  });

  it('computes a trailing window that never exceeds 30 days', () => {
    const now = Date.parse('2026-10-28T12:00:00Z');
    const window = windowFor('30d', now);
    expect(window.end).toBe('2026-10-28T12:00:00.000Z');
    expect(Date.parse(window.end) - Date.parse(window.start)).toBe(30 * 24 * 3_600_000);
  });
});

describe('rule descriptions', () => {
  const base = {
    consecutive_failures: null,
    operator: null,
    threshold: null,
    status_condition: null,
    recovery_successes: 1,
  } as const;

  it('describes each rule type in plain language', () => {
    expect(
      describeTrigger({ ...base, rule_type: 'CONSECUTIVE_FAILURES', consecutive_failures: 3 }),
    ).toBe('3 consecutive unhealthy checks');
    expect(
      describeTrigger({ ...base, rule_type: 'CONSECUTIVE_FAILURES', consecutive_failures: 1 }),
    ).toBe('1 consecutive unhealthy check');
    expect(describeTrigger({ ...base, rule_type: 'STATUS', status_condition: 'UNHEALTHY' })).toBe(
      'Immediately when status is degraded or down',
    );
    expect(
      describeTrigger({
        ...base,
        rule_type: 'LATENCY_THRESHOLD',
        operator: 'GREATER_THAN_OR_EQUAL',
        threshold: 500,
        consecutive_failures: 2,
      }),
    ).toBe('Response time ≥ 500 ms for 2 consecutive checks');
  });

  it('describes recovery per type', () => {
    expect(describeRecovery({ ...base, rule_type: 'STATUS', recovery_successes: 2 })).toBe(
      'Recovers after 2 healthy checks',
    );
    expect(
      describeRecovery({ ...base, rule_type: 'LATENCY_THRESHOLD', recovery_successes: 1 }),
    ).toBe('Recovers after 1 check within the threshold');
  });

  it('labels every resolution reason and never presents MANUAL as a person', () => {
    expect(Object.keys(RESOLUTION_LABELS)).toHaveLength(6);
    expect(actorLabel('SYSTEM')).toBe('System');
    expect(actorLabel('MANUAL')).toMatch(/no verified identity/);
    expect(MATERIAL_FIELDS).toContain('threshold');
  });
});

describe('status ordering', () => {
  it('sorts the most severe status first', () => {
    expect(STATUS_ORDER.DOWN).toBeLessThan(STATUS_ORDER.DEGRADED);
    expect(STATUS_ORDER.DEGRADED).toBeLessThan(STATUS_ORDER.UNKNOWN);
    expect(STATUS_ORDER.UNKNOWN).toBeLessThan(STATUS_ORDER.HEALTHY);
  });
});

import { describe, expect, it } from 'vitest';
import { makeResource } from '../../test/fixtures.ts';
import {
  buildCreate,
  buildUpdate,
  DEFAULT_VALUES,
  resetsObservation,
  validateResourceForm,
  valuesFromResource,
  type ResourceFormValues,
} from './form.ts';

const valid: ResourceFormValues = {
  ...DEFAULT_VALUES,
  name: 'Checkout API',
  url: 'https://checkout.example.com/health',
};

describe('validateResourceForm', () => {
  it('accepts a complete, sensible form', () => {
    expect(validateResourceForm(valid)).toEqual({});
  });

  it('requires a name and a URL', () => {
    const errors = validateResourceForm({ ...valid, name: '  ', url: '' });
    expect(errors.name).toBeDefined();
    expect(errors.url).toBeDefined();
  });

  it('rejects over-long names and descriptions', () => {
    expect(validateResourceForm({ ...valid, name: 'x'.repeat(101) }).name).toBeDefined();
    expect(
      validateResourceForm({ ...valid, description: 'x'.repeat(501) }).description,
    ).toBeDefined();
  });

  it.each(['example.com', 'ftp://example.com', 'javascript:alert(1)', 'not a url'])(
    'rejects the URL %s',
    (url) => {
      expect(validateResourceForm({ ...valid, url }).url).toBeDefined();
    },
  );

  it('accepts http and https URLs', () => {
    expect(validateResourceForm({ ...valid, url: 'http://demo-target:8080/' }).url).toBeUndefined();
  });

  it('requires whole-number intervals and timeouts', () => {
    expect(validateResourceForm({ ...valid, interval: '1.5' }).interval).toBeDefined();
    expect(validateResourceForm({ ...valid, interval: '0' }).interval).toBeDefined();
    expect(validateResourceForm({ ...valid, timeout: 'abc' }).timeout).toBeDefined();
  });

  it('rejects a timeout longer than the interval', () => {
    const errors = validateResourceForm({ ...valid, interval: '5', timeout: '10' });
    expect(errors.timeout).toMatch(/longer than the check interval/);
  });

  it('keeps the expected status in the HTTP range', () => {
    expect(validateResourceForm({ ...valid, expectedStatus: '99' }).expectedStatus).toBeDefined();
    expect(validateResourceForm({ ...valid, expectedStatus: '600' }).expectedStatus).toBeDefined();
    expect(
      validateResourceForm({ ...valid, expectedStatus: '204' }).expectedStatus,
    ).toBeUndefined();
  });
});

describe('request building', () => {
  it('trims input and converts numbers for a create', () => {
    expect(
      buildCreate({
        ...valid,
        name: '  Checkout  ',
        description: '  ',
        interval: '30',
        timeout: '5',
        expectedStatus: '204',
        enabled: false,
      }),
    ).toEqual({
      name: 'Checkout',
      url: 'https://checkout.example.com/health',
      description: null,
      is_enabled: false,
      check_interval_seconds: 30,
      timeout_seconds: 5,
      expected_status_code: 204,
    });
  });

  it('sends nothing for an unchanged edit', () => {
    const resource = makeResource({ description: 'Payments' });
    expect(buildUpdate(valuesFromResource(resource), resource)).toEqual({});
  });

  it('sends only the fields that changed', () => {
    const resource = makeResource();
    const values = { ...valuesFromResource(resource), name: 'Renamed', interval: '120' };
    expect(buildUpdate(values, resource)).toEqual({ name: 'Renamed', check_interval_seconds: 120 });
  });

  it('can clear a description', () => {
    const resource = makeResource({ description: 'old' });
    const values = { ...valuesFromResource(resource), description: '' };
    expect(buildUpdate(values, resource)).toEqual({ description: null });
  });

  it('flags edits that reset the last observation', () => {
    expect(resetsObservation({ url: 'https://x.example.com' })).toBe(true);
    expect(resetsObservation({ timeout_seconds: 3 })).toBe(true);
    expect(resetsObservation({ expected_status_code: 204 })).toBe(true);
    expect(resetsObservation({ name: 'x', check_interval_seconds: 30 })).toBe(false);
  });
});

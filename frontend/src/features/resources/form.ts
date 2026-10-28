import type { Resource, ResourceCreate, ResourceUpdate } from '../../lib/api/types.ts';

export interface ResourceFormValues {
  name: string;
  url: string;
  description: string;
  interval: string;
  timeout: string;
  expectedStatus: string;
  enabled: boolean;
}

export type ResourceFormField = keyof Omit<ResourceFormValues, 'enabled'>;
export type ResourceFormErrors = Partial<Record<ResourceFormField, string>>;

// The same defaults the backend applies (settings may differ; the server stays the authority).
export const DEFAULT_VALUES: ResourceFormValues = {
  name: '',
  url: '',
  description: '',
  interval: '60',
  timeout: '10',
  expectedStatus: '200',
  enabled: true,
};

export function valuesFromResource(resource: Resource): ResourceFormValues {
  return {
    name: resource.name,
    url: resource.url,
    description: resource.description ?? '',
    interval: String(resource.check_interval_seconds),
    timeout: String(resource.timeout_seconds),
    expectedStatus: String(resource.expected_status_code),
    enabled: resource.is_enabled,
  };
}

/** Maps backend field names (from a 422) to this form's fields. */
export const SERVER_FIELD_MAP: Record<string, ResourceFormField> = {
  name: 'name',
  url: 'url',
  description: 'description',
  check_interval_seconds: 'interval',
  timeout_seconds: 'timeout',
  expected_status_code: 'expectedStatus',
};

const isWholeNumber = (text: string) => /^\d+$/.test(text.trim());

/**
 * Immediate, friendly checks. These only catch obvious mistakes: the backend remains the
 * authority (URL safety policy, allowed interval range, rule limits) and its errors are shown too.
 */
export function validateResourceForm(values: ResourceFormValues): ResourceFormErrors {
  const errors: ResourceFormErrors = {};
  const name = values.name.trim();
  if (!name) errors.name = 'Enter a name.';
  else if (name.length > 100) errors.name = 'Use 100 characters or fewer.';

  const url = values.url.trim();
  if (!url) errors.url = 'Enter the URL to check.';
  else {
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        errors.url = 'Use an http:// or https:// URL.';
      }
    } catch {
      errors.url = 'Enter a full URL such as https://example.com/health.';
    }
  }

  if (values.description.length > 500) errors.description = 'Use 500 characters or fewer.';

  if (!isWholeNumber(values.interval) || Number(values.interval) < 1) {
    errors.interval = 'Enter a whole number of seconds (1 or more).';
  }
  if (!isWholeNumber(values.timeout) || Number(values.timeout) < 1) {
    errors.timeout = 'Enter a whole number of seconds (1 or more).';
  }
  if (!errors.interval && !errors.timeout && Number(values.timeout) > Number(values.interval)) {
    errors.timeout = 'The timeout cannot be longer than the check interval.';
  }

  const code = Number(values.expectedStatus);
  if (!isWholeNumber(values.expectedStatus) || code < 100 || code > 599) {
    errors.expectedStatus = 'Enter an HTTP status code between 100 and 599.';
  }
  return errors;
}

export function buildCreate(values: ResourceFormValues): ResourceCreate {
  return {
    name: values.name.trim(),
    url: values.url.trim(),
    description: values.description.trim() || null,
    is_enabled: values.enabled,
    check_interval_seconds: Number(values.interval),
    timeout_seconds: Number(values.timeout),
    expected_status_code: Number(values.expectedStatus),
  };
}

/** A PATCH containing only what changed, so an edit never overwrites fields it did not touch. */
export function buildUpdate(values: ResourceFormValues, resource: Resource): ResourceUpdate {
  const next = buildCreate(values);
  const update: ResourceUpdate = {};
  if (next.name !== resource.name) update.name = next.name;
  if (next.url !== resource.url) update.url = next.url;
  if ((next.description ?? null) !== resource.description)
    update.description = next.description ?? null;
  if (next.is_enabled !== resource.is_enabled) update.is_enabled = next.is_enabled;
  if (next.check_interval_seconds !== resource.check_interval_seconds) {
    update.check_interval_seconds = next.check_interval_seconds as number;
  }
  if (next.timeout_seconds !== resource.timeout_seconds) {
    update.timeout_seconds = next.timeout_seconds as number;
  }
  if (next.expected_status_code !== resource.expected_status_code) {
    update.expected_status_code = next.expected_status_code as number;
  }
  return update;
}

/** True if the edit changes what is checked, which resets the last observation server-side. */
export function resetsObservation(update: ResourceUpdate): boolean {
  return (
    update.url !== undefined ||
    update.timeout_seconds !== undefined ||
    update.expected_status_code !== undefined
  );
}

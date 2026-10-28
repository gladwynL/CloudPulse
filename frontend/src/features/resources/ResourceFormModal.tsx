import { useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/Button.tsx';
import { Modal } from '../../components/ui/Dialog.tsx';
import { CheckboxField, TextAreaField, TextField } from '../../components/ui/Fields.tsx';
import { Notice } from '../../components/ui/States.tsx';
import { useToast } from '../../components/ui/useToast.ts';
import { ApiError, errorMessage } from '../../lib/api/client.ts';
import type { Resource } from '../../lib/api/types.ts';
import {
  DEFAULT_VALUES,
  SERVER_FIELD_MAP,
  buildCreate,
  buildUpdate,
  resetsObservation,
  validateResourceForm,
  valuesFromResource,
  type ResourceFormErrors,
  type ResourceFormValues,
} from './form.ts';
import { useCreateResource, useUpdateResource } from './hooks.ts';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Present when editing; absent when creating. */
  resource?: Resource;
  onSaved?: (resource: Resource) => void;
}

/** The form mounts only while open, so each opening starts from fresh state. */
export function ResourceFormModal({ open, onClose, resource, onSaved }: Props) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={resource ? `Edit ${resource.name}` : 'Add resource'}
      description={
        resource
          ? 'Changes apply to the next check.'
          : 'CloudPulse will check this URL on a schedule and keep its history.'
      }
    >
      <ResourceForm
        key={resource?.id ?? 'new'}
        resource={resource}
        onClose={onClose}
        onSaved={onSaved}
      />
    </Modal>
  );
}

function ResourceForm({
  resource,
  onClose,
  onSaved,
}: {
  resource?: Resource;
  onClose: () => void;
  onSaved?: (resource: Resource) => void;
}) {
  const toast = useToast();
  const create = useCreateResource();
  const update = useUpdateResource(resource?.id ?? '');
  const [values, setValues] = useState<ResourceFormValues>(
    resource ? valuesFromResource(resource) : DEFAULT_VALUES,
  );
  const [errors, setErrors] = useState<ResourceFormErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const pending = create.isPending || update.isPending;

  const set = <K extends keyof ResourceFormValues>(key: K, value: ResourceFormValues[K]) => {
    setValues((current) => ({ ...current, [key]: value }));
    if (key in errors) setErrors((current) => ({ ...current, [key]: undefined }));
  };

  const pendingUpdate = resource ? buildUpdate(values, resource) : null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const found = validateResourceForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    const handleError = (error: unknown) => {
      if (error instanceof ApiError && Object.keys(error.fieldErrors).length > 0) {
        const mapped: ResourceFormErrors = {};
        for (const [field, message] of Object.entries(error.fieldErrors)) {
          const target = SERVER_FIELD_MAP[field];
          if (target) mapped[target] = message;
        }
        setErrors(mapped);
        if (Object.keys(mapped).length > 0) return;
      }
      setFormError(errorMessage(error));
    };

    if (resource) {
      if (!pendingUpdate || Object.keys(pendingUpdate).length === 0) {
        onClose();
        return;
      }
      update.mutate(pendingUpdate, {
        onSuccess: (saved) => {
          toast.success(`Saved “${saved.name}”.`);
          onSaved?.(saved);
          onClose();
        },
        onError: handleError,
      });
    } else {
      create.mutate(buildCreate(values), {
        onSuccess: (saved) => {
          toast.success(`Added “${saved.name}”. The first check runs within seconds.`);
          onSaved?.(saved);
          onClose();
        },
        onError: handleError,
      });
    }
  };

  return (
    <form onSubmit={onSubmit} noValidate>
      <div className="dialog__body">
        {formError && (
          <Notice tone="error" title="Could not save">
            {formError}
          </Notice>
        )}
        <div className="form-grid">
          <TextField
            className="span-2"
            label="Name"
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
            error={errors.name}
            maxLength={100}
            autoComplete="off"
            autoFocus
            required
          />
          <TextField
            className="span-2"
            label="URL to check"
            type="url"
            inputMode="url"
            value={values.url}
            onChange={(e) => set('url', e.target.value)}
            error={errors.url}
            placeholder="https://example.com/health"
            hint="Checked with HTTP GET. Private, loopback and cloud-metadata addresses are blocked."
            autoComplete="off"
            required
          />
          <TextField
            label="Check interval (seconds)"
            inputMode="numeric"
            value={values.interval}
            onChange={(e) => set('interval', e.target.value)}
            error={errors.interval}
            required
          />
          <TextField
            label="Timeout (seconds)"
            inputMode="numeric"
            value={values.timeout}
            onChange={(e) => set('timeout', e.target.value)}
            error={errors.timeout}
            required
          />
          <TextField
            label="Expected HTTP status"
            inputMode="numeric"
            value={values.expectedStatus}
            onChange={(e) => set('expectedStatus', e.target.value)}
            error={errors.expectedStatus}
            hint="Any other status is reported as Degraded."
            required
          />
          <CheckboxField
            label="Monitoring enabled"
            checked={values.enabled}
            onChange={(e) => set('enabled', e.target.checked)}
            hint="Disabled resources are not checked; their history is kept."
          />
          <TextAreaField
            className="span-2"
            label="Description (optional)"
            value={values.description}
            onChange={(e) => set('description', e.target.value)}
            error={errors.description}
            maxLength={500}
          />
        </div>
        {pendingUpdate && resetsObservation(pendingUpdate) && (
          <Notice tone="info">
            Changing the URL, timeout or expected status resets the last observation: the status
            shows Unknown until the next check. Existing history is kept.
          </Notice>
        )}
        {resource && pendingUpdate?.is_enabled === false && (
          <Notice tone="warning">
            Disabling monitoring resolves this resource&apos;s active incidents (reason: resource
            disabled).
          </Notice>
        )}
      </div>
      <div className="dialog__foot">
        <Button onClick={onClose} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={pending}>
          {resource ? 'Save changes' : 'Add resource'}
        </Button>
      </div>
    </form>
  );
}

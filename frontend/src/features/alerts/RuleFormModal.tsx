import { useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/Button.tsx';
import { Modal } from '../../components/ui/Dialog.tsx';
import {
  CheckboxField,
  SelectField,
  TextAreaField,
  TextField,
} from '../../components/ui/Fields.tsx';
import { Notice } from '../../components/ui/States.tsx';
import { useToast } from '../../components/ui/useToast.ts';
import { ApiError, errorMessage } from '../../lib/api/client.ts';
import type { AlertRule, AlertRuleType } from '../../lib/api/types.ts';
import {
  OPERATOR_LABELS,
  RULE_TYPE_HINTS,
  RULE_TYPE_LABELS,
  STATUS_CONDITION_LABELS,
} from '../../lib/utils/rules.ts';
import { useOverview } from '../overview/hooks.ts';
import { useResources } from '../resources/hooks.ts';
import {
  DEFAULT_RULE_VALUES,
  RULE_SERVER_FIELD_MAP,
  buildRuleCreate,
  buildRuleUpdate,
  fieldsFor,
  resolvesActiveIncident,
  validateRuleForm,
  valuesFromRule,
  type RuleFormErrors,
  type RuleFormValues,
} from './form.ts';
import { useCreateRule, useResourceRules, useUpdateRule } from './hooks.ts';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Editing an existing rule (its type and resource cannot change). */
  rule?: AlertRule;
  /** Creating on a known resource (resource page); otherwise a resource picker is shown. */
  resourceId?: string;
}

export function RuleFormModal({ open, onClose, rule, resourceId }: Props) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={rule ? `Edit rule “${rule.name}”` : 'Create alert rule'}
      description={
        rule
          ? 'The rule type cannot be changed; delete and recreate it to switch type.'
          : 'A new rule evaluates future checks only; it never replays history.'
      }
    >
      <RuleForm
        key={rule?.id ?? 'new'}
        rule={rule}
        presetResourceId={resourceId}
        onClose={onClose}
      />
    </Modal>
  );
}

function RuleForm({
  rule,
  presetResourceId,
  onClose,
}: {
  rule?: AlertRule;
  presetResourceId?: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const create = useCreateRule();
  const update = useUpdateRule();
  const overview = useOverview();
  const [values, setValues] = useState<RuleFormValues>(
    rule ? valuesFromRule(rule) : DEFAULT_RULE_VALUES,
  );
  const [errors, setErrors] = useState<RuleFormErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [chosenResource, setChosenResource] = useState(presetResourceId ?? '');
  const pending = create.isPending || update.isPending;

  // Resource picker (create without a preset). The API caps a page at 200 resources.
  const needsPicker = !rule && !presetResourceId;
  const resources = useResources({ limit: 200 });
  const resourceId = rule?.resource_id ?? chosenResource;
  const existingRules = useResourceRules(rule ? undefined : resourceId || undefined);
  const limit = overview.data?.limits.max_rules_per_resource;
  const used = existingRules.data?.total ?? 0;
  const atLimit = !rule && limit !== undefined && Boolean(resourceId) && used >= limit;

  const set = <K extends keyof RuleFormValues>(key: K, value: RuleFormValues[K]) => {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key as string]: undefined }));
  };

  const show = fieldsFor(values.ruleType);
  const pendingUpdate = rule ? buildRuleUpdate(values, rule) : null;
  const warnsResolution =
    rule && pendingUpdate ? resolvesActiveIncident(pendingUpdate, rule) : false;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    if (needsPicker && !chosenResource) {
      setFormError('Choose the resource this rule applies to.');
      return;
    }
    const found = validateRuleForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    const handleError = (error: unknown) => {
      if (error instanceof ApiError && Object.keys(error.fieldErrors).length > 0) {
        const mapped: RuleFormErrors = {};
        for (const [field, message] of Object.entries(error.fieldErrors)) {
          const target = RULE_SERVER_FIELD_MAP[field];
          if (target) mapped[target] = message;
        }
        setErrors(mapped);
        if (Object.keys(mapped).length > 0) return;
      }
      setFormError(errorMessage(error));
    };

    if (rule) {
      if (!pendingUpdate || Object.keys(pendingUpdate).length === 0) {
        onClose();
        return;
      }
      update.mutate(
        { id: rule.id, resourceId: rule.resource_id, body: pendingUpdate },
        {
          onSuccess: (saved) => {
            toast.success(
              warnsResolution
                ? `Saved “${saved.name}”. Its active incident was resolved.`
                : `Saved “${saved.name}”.`,
            );
            onClose();
          },
          onError: handleError,
        },
      );
    } else {
      create.mutate(
        { resourceId, body: buildRuleCreate(values) },
        {
          onSuccess: (saved) => {
            toast.success(`Created rule “${saved.name}”.`);
            onClose();
          },
          onError: handleError,
        },
      );
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

        {needsPicker && (
          <SelectField
            label="Resource"
            value={chosenResource}
            onChange={(e) => setChosenResource(e.target.value)}
            required
            hint={
              resources.data && resources.data.total > 200
                ? `Showing the first 200 of ${resources.data.total} resources.`
                : undefined
            }
          >
            <option value="">
              {resources.isPending ? 'Loading resources…' : 'Choose a resource'}
            </option>
            {resources.data?.items.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </SelectField>
        )}

        {atLimit && (
          <Notice tone="warning" title="Rule limit reached">
            This resource already has {used} of {limit} rules. Delete a rule before adding another.
          </Notice>
        )}
        {!rule && resourceId && limit !== undefined && !atLimit && existingRules.data && (
          <p className="chart-note">
            This resource has {used} of {limit} allowed rules.
          </p>
        )}

        <div className="form-grid">
          <TextField
            className="span-2"
            label="Rule name"
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
            error={errors.name}
            maxLength={100}
            autoComplete="off"
            required
          />

          {rule ? (
            <div className="field span-2">
              <span className="field__label">Rule type</span>
              <p>
                <strong>{RULE_TYPE_LABELS[rule.rule_type]}</strong>
              </p>
              <p className="field__hint">{RULE_TYPE_HINTS[rule.rule_type]}</p>
            </div>
          ) : (
            <SelectField
              className="span-2"
              label="Rule type"
              value={values.ruleType}
              onChange={(e) => set('ruleType', e.target.value as AlertRuleType)}
              hint={RULE_TYPE_HINTS[values.ruleType]}
            >
              {(Object.keys(RULE_TYPE_LABELS) as AlertRuleType[]).map((type) => (
                <option key={type} value={type}>
                  {RULE_TYPE_LABELS[type]}
                </option>
              ))}
            </SelectField>
          )}

          {show.status && (
            <SelectField
              label="Fire when status is"
              value={values.statusCondition}
              onChange={(e) =>
                set('statusCondition', e.target.value as RuleFormValues['statusCondition'])
              }
              error={errors.statusCondition}
            >
              {Object.entries(STATUS_CONDITION_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </SelectField>
          )}

          {show.latency && (
            <>
              <SelectField
                label="Response time is"
                value={values.operator}
                onChange={(e) => set('operator', e.target.value as RuleFormValues['operator'])}
                error={errors.operator}
              >
                {Object.entries(OPERATOR_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </SelectField>
              <TextField
                label="Threshold (milliseconds)"
                inputMode="decimal"
                value={values.threshold}
                onChange={(e) => set('threshold', e.target.value)}
                error={errors.threshold}
                required
              />
            </>
          )}

          {show.consecutive && (
            <TextField
              label={
                values.ruleType === 'LATENCY_THRESHOLD'
                  ? 'Consecutive slow checks to trigger'
                  : 'Consecutive failed checks to trigger'
              }
              inputMode="numeric"
              value={values.consecutiveFailures}
              onChange={(e) => set('consecutiveFailures', e.target.value)}
              error={errors.consecutiveFailures}
              hint="1 to 20"
              required
            />
          )}

          <TextField
            label={
              values.ruleType === 'LATENCY_THRESHOLD'
                ? 'Fast checks needed to recover'
                : 'Healthy checks needed to recover'
            }
            inputMode="numeric"
            value={values.recoverySuccesses}
            onChange={(e) => set('recoverySuccesses', e.target.value)}
            error={errors.recoverySuccesses}
            hint="1 to 20, in a row"
            required
          />

          <SelectField
            label="Severity"
            value={values.severity}
            onChange={(e) => set('severity', e.target.value as RuleFormValues['severity'])}
          >
            <option value="WARNING">Warning</option>
            <option value="CRITICAL">Critical</option>
          </SelectField>

          <CheckboxField
            label="Rule enabled"
            checked={values.enabled}
            onChange={(e) => set('enabled', e.target.checked)}
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

        {rule?.current_state === 'FIRING' && !warnsResolution && (
          <Notice tone="warning" title="This rule is firing right now">
            Disabling it, or changing its trigger or recovery settings, will resolve its active
            incident. Renaming and changing severity will not.
          </Notice>
        )}
        {warnsResolution && (
          <Notice tone="warning" title="This will resolve the active incident">
            Saving these changes resolves the incident this rule has open, and resets the
            rule&apos;s counters. The incident stays in the incident history.
          </Notice>
        )}
      </div>
      <div className="dialog__foot">
        <Button onClick={onClose} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={pending} disabled={atLimit}>
          {rule ? 'Save changes' : 'Create rule'}
        </Button>
      </div>
    </form>
  );
}

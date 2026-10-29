import { Pause, Pencil, Play, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AlertStateBadge, EnabledBadge, SeverityBadge } from '../../components/ui/Badges.tsx';
import { Button } from '../../components/ui/Button.tsx';
import { ConfirmDialog } from '../../components/ui/Dialog.tsx';
import { FilterSelect } from '../../components/ui/Fields.tsx';
import { OffsetPager } from '../../components/ui/Pager.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, ErrorState, LoadingState, Refreshing } from '../../components/ui/States.tsx';
import { RelativeTime } from '../../components/ui/Time.tsx';
import { useToast } from '../../components/ui/useToast.ts';
import { errorMessage } from '../../lib/api/client.ts';
import type { AlertRuleListItem, AlertSeverity, AlertState } from '../../lib/api/types.ts';
import { RULE_TYPE_LABELS, describeRecovery, describeTrigger } from '../../lib/utils/rules.ts';
import { RuleFormModal } from './RuleFormModal.tsx';
import { useAllRules, useDeleteRule, useUpdateRule } from './hooks.ts';

const PAGE_SIZE = 25;
const STATES: AlertState[] = ['FIRING', 'OK', 'INSUFFICIENT_DATA'];
const STATE_LABELS: Record<AlertState, string> = {
  FIRING: 'Firing',
  OK: 'OK',
  INSUFFICIENT_DATA: 'Insufficient data',
};

function RuleRow({
  rule,
  onEdit,
  onDelete,
}: {
  rule: AlertRuleListItem;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const toast = useToast();
  const update = useUpdateRule();
  const toggle = () =>
    update.mutate(
      { id: rule.id, resourceId: rule.resource_id, body: { is_enabled: !rule.is_enabled } },
      {
        onSuccess: (saved) =>
          toast.success(
            saved.is_enabled
              ? `Rule “${saved.name}” enabled.`
              : `Rule “${saved.name}” disabled${rule.current_state === 'FIRING' ? '; its active incident was resolved' : ''}.`,
          ),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );

  return (
    <tr>
      <td data-label="Rule">
        <div className="cell-title">
          <span style={{ fontWeight: 600 }}>{rule.name}</span>
          {rule.description && (
            <span className="cell-sub truncate" style={{ maxWidth: 240 }}>
              {rule.description}
            </span>
          )}
        </div>
      </td>
      <td data-label="Resource">
        <Link to={`/resources/${rule.resource_id}`}>{rule.resource_name}</Link>
      </td>
      <td data-label="Condition">
        <div className="cell-title">
          <span>{RULE_TYPE_LABELS[rule.rule_type]}</span>
          <span className="cell-sub">{describeTrigger(rule)}</span>
          <span className="cell-sub">{describeRecovery(rule)}</span>
        </div>
      </td>
      <td data-label="Severity">
        <SeverityBadge severity={rule.severity} />
      </td>
      <td data-label="State">
        <div className="cell-title">
          <AlertStateBadge state={rule.current_state} />
          <span className="cell-sub">
            streak {rule.failure_streak}
            {rule.current_state === 'FIRING'
              ? ` · recovery ${rule.recovery_streak}/${rule.recovery_successes}`
              : ''}
          </span>
        </div>
      </td>
      <td className="nowrap" data-label="Last evaluated">
        <RelativeTime iso={rule.last_evaluated_at} empty="not yet" />
      </td>
      <td data-label="Status">
        <EnabledBadge enabled={rule.is_enabled} />
      </td>
      <td data-label="">
        <div className="actions">
          <Button
            small
            iconOnly
            aria-label={rule.is_enabled ? `Disable rule ${rule.name}` : `Enable rule ${rule.name}`}
            title={rule.is_enabled ? 'Disable' : 'Enable'}
            onClick={toggle}
            loading={update.isPending}
          >
            {!update.isPending &&
              (rule.is_enabled ? (
                <Pause size={14} aria-hidden="true" />
              ) : (
                <Play size={14} aria-hidden="true" />
              ))}
          </Button>
          <Button
            small
            iconOnly
            aria-label={`Edit rule ${rule.name}`}
            title="Edit"
            onClick={onEdit}
          >
            <Pencil size={14} aria-hidden="true" />
          </Button>
          <Button
            small
            iconOnly
            variant="danger"
            aria-label={`Delete rule ${rule.name}`}
            title="Delete"
            onClick={onDelete}
          >
            <Trash2 size={14} aria-hidden="true" />
          </Button>
        </div>
      </td>
    </tr>
  );
}

export function AlertsPage() {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const state = STATES.includes(params.get('state') as AlertState)
    ? (params.get('state') as AlertState)
    : undefined;
  const severityParam = params.get('severity');
  const severity: AlertSeverity | undefined =
    severityParam === 'WARNING' || severityParam === 'CRITICAL' ? severityParam : undefined;
  const enabledParam = params.get('enabled');
  const isEnabled = enabledParam === 'true' ? true : enabledParam === 'false' ? false : undefined;
  const resourceId = params.get('resource') ?? undefined;
  const offset = Math.max(0, Number(params.get('offset')) || 0);

  const update = (changes: Record<string, string | null>) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const [key, value] of Object.entries(changes)) {
          if (value === null || value === '') next.delete(key);
          else next.set(key, value);
        }
        return next;
      },
      { replace: true },
    );

  const list = useAllRules({
    limit: PAGE_SIZE,
    offset,
    current_state: state,
    severity,
    is_enabled: isEnabled,
    resource_id: resourceId,
  });
  const remove = useDeleteRule();

  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<AlertRuleListItem | null>(null);
  const [deleting, setDeleting] = useState<AlertRuleListItem | null>(null);
  const filtered = Boolean(state || severity || isEnabled !== undefined || resourceId);

  const confirmDelete = () => {
    if (!deleting) return;
    remove.mutate(
      { id: deleting.id, resourceId: deleting.resource_id },
      {
        onSuccess: () => {
          toast.success(`Deleted rule “${deleting.name}”.`);
          setDeleting(null);
        },
        onError: (error) => {
          toast.error(errorMessage(error));
          setDeleting(null);
        },
      },
    );
  };

  return (
    <div className="page">
      <PageHeader
        title="Alert rules"
        subtitle="Conditions that open incidents when a resource misbehaves"
        actions={
          <>
            <Refreshing active={list.isFetching && !list.isPending} />
            <Button small onClick={() => void list.refetch()} aria-label="Refresh alert rules">
              <RefreshCw size={14} aria-hidden="true" /> Refresh
            </Button>
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus size={16} aria-hidden="true" /> Create rule
            </Button>
          </>
        }
      />

      <div className="toolbar" role="search" aria-label="Filter alert rules">
        <FilterSelect
          label="State"
          value={state ?? ''}
          onChange={(e) => update({ state: e.target.value || null, offset: null })}
        >
          <option value="">All states</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {STATE_LABELS[s]}
            </option>
          ))}
        </FilterSelect>
        <FilterSelect
          label="Severity"
          value={severity ?? ''}
          onChange={(e) => update({ severity: e.target.value || null, offset: null })}
        >
          <option value="">All severities</option>
          <option value="CRITICAL">Critical</option>
          <option value="WARNING">Warning</option>
        </FilterSelect>
        <FilterSelect
          label="Rule status"
          value={enabledParam ?? ''}
          onChange={(e) => update({ enabled: e.target.value || null, offset: null })}
        >
          <option value="">Enabled and disabled</option>
          <option value="true">Enabled only</option>
          <option value="false">Disabled only</option>
        </FilterSelect>
        {resourceId && (
          <Button variant="ghost" onClick={() => update({ resource: null, offset: null })}>
            Showing one resource · show all
          </Button>
        )}
        {filtered && (
          <Button variant="ghost" onClick={() => setParams({}, { replace: true })}>
            Clear filters
          </Button>
        )}
      </div>

      {list.isPending ? (
        <LoadingState label="Loading alert rules…" />
      ) : list.isError && !list.data ? (
        <ErrorState
          error={list.error}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
        />
      ) : list.data.total === 0 ? (
        filtered ? (
          <EmptyState title="No rules match these filters" />
        ) : (
          <EmptyState
            title="No alert rules yet"
            action={
              <Button variant="primary" onClick={() => setCreating(true)}>
                <Plus size={16} aria-hidden="true" /> Create your first rule
              </Button>
            }
          >
            Rules watch a resource&apos;s checks and open an incident when a condition holds, for
            example three failures in a row.
          </EmptyState>
        )
      ) : (
        <>
          <div className="table-wrap table-wrap--stack">
            <table className="table table--stack">
              <caption className="sr-only">Alert rules</caption>
              <thead>
                <tr>
                  <th scope="col">Rule</th>
                  <th scope="col">Resource</th>
                  <th scope="col">Condition</th>
                  <th scope="col">Severity</th>
                  <th scope="col">State</th>
                  <th scope="col">Last evaluated</th>
                  <th scope="col">Status</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((rule) => (
                  <RuleRow
                    key={rule.id}
                    rule={rule}
                    onEdit={() => setEditing(rule)}
                    onDelete={() => setDeleting(rule)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <OffsetPager
            offset={list.data.offset}
            limit={list.data.limit}
            total={list.data.total}
            noun="rules"
            onChange={(next) => update({ offset: next ? String(next) : null })}
          />
        </>
      )}

      <RuleFormModal
        open={creating || editing !== null}
        rule={editing ?? undefined}
        resourceId={resourceId}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
      />

      <ConfirmDialog
        open={deleting !== null}
        title={`Delete rule “${deleting?.name ?? ''}”?`}
        confirmLabel="Delete rule"
        danger
        pending={remove.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      >
        <p>
          The rule stops evaluating.
          {deleting?.current_state === 'FIRING' && (
            <>
              {' '}
              <strong>It is firing now: its active incident will be resolved</strong> (reason: rule
              deleted).
            </>
          )}{' '}
          Past incidents are kept.
        </p>
      </ConfirmDialog>
    </div>
  );
}

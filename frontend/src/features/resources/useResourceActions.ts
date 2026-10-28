import { useToast } from '../../components/ui/useToast.ts';
import { errorMessage } from '../../lib/api/client.ts';
import type { Resource } from '../../lib/api/types.ts';
import { formatMs } from '../../lib/utils/format.ts';
import { useCheckResource, useUpdateResource } from './hooks.ts';

const STATUS_WORDS = {
  HEALTHY: 'healthy',
  DEGRADED: 'degraded',
  DOWN: 'down',
  UNKNOWN: 'unknown',
} as const;

/** Manual check and enable/disable, with consistent feedback, shared by the list and detail pages. */
export function useResourceActions(resource: Resource) {
  const toast = useToast();
  const check = useCheckResource();
  const update = useUpdateResource(resource.id);

  const runCheck = () =>
    check.mutate(resource.id, {
      onSuccess: (status) => {
        const latency =
          status.last_response_time_ms !== null
            ? ` (${formatMs(status.last_response_time_ms)})`
            : '';
        const message = `${resource.name} is ${STATUS_WORDS[status.current_status]}${latency}.`;
        if (status.current_status === 'HEALTHY') toast.success(message);
        else if (status.last_error) toast.error(`${message} ${status.last_error}`);
        else toast.info(message);
      },
      // 409: disabled, a check already running, or the resource changed mid-check.
      onError: (error) => toast.error(errorMessage(error)),
    });

  const toggleEnabled = () =>
    update.mutate(
      { is_enabled: !resource.is_enabled },
      {
        onSuccess: (saved) =>
          toast.success(
            saved.is_enabled
              ? `Monitoring of “${saved.name}” enabled.`
              : `Monitoring of “${saved.name}” disabled.`,
          ),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );

  return {
    runCheck,
    toggleEnabled,
    checking: check.isPending,
    toggling: update.isPending,
  };
}

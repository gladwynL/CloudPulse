import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';
import { api } from '../../lib/api/endpoints.ts';
import type { AlertRuleCreate, AlertRuleListParams, AlertRuleUpdate } from '../../lib/api/types.ts';
import { polling } from '../../lib/polling.ts';
import { keys } from '../../lib/queryKeys.ts';

export function useAllRules(params: AlertRuleListParams) {
  return useQuery({
    queryKey: keys.ruleList(params),
    queryFn: ({ signal }) => api.listAllRules(params, { signal }),
    refetchInterval: polling.rules,
    placeholderData: keepPreviousData,
  });
}

/** A resource's rules (max 100 per page; the backend caps rules per resource far below that). */
export function useResourceRules(resourceId: string | undefined) {
  return useQuery({
    queryKey: keys.resourceRules(resourceId ?? 'none'),
    queryFn: ({ signal }) => api.listResourceRules(resourceId!, { limit: 100 }, { signal }),
    enabled: Boolean(resourceId),
    refetchInterval: polling.rules,
  });
}

/** Rule changes can resolve incidents and change firing counts, so refresh those too. */
function invalidateAfterRuleChange(queryClient: QueryClient, resourceId?: string) {
  const invalidations = [
    queryClient.invalidateQueries({ queryKey: keys.rules }),
    queryClient.invalidateQueries({ queryKey: keys.overview }),
    queryClient.invalidateQueries({ queryKey: keys.incidents }),
  ];
  if (resourceId) {
    invalidations.push(queryClient.invalidateQueries({ queryKey: keys.resourceRules(resourceId) }));
  }
  return Promise.all(invalidations);
}

export function useCreateRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ resourceId, body }: { resourceId: string; body: AlertRuleCreate }) =>
      api.createRule(resourceId, body),
    onSuccess: (_rule, { resourceId }) => invalidateAfterRuleChange(queryClient, resourceId),
  });
}

export function useUpdateRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; resourceId: string; body: AlertRuleUpdate }) =>
      api.updateRule(id, body),
    onSuccess: (_rule, { resourceId }) => invalidateAfterRuleChange(queryClient, resourceId),
  });
}

export function useDeleteRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string; resourceId: string }) => api.deleteRule(id),
    onSuccess: (_data, { resourceId }) => invalidateAfterRuleChange(queryClient, resourceId),
  });
}

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';
import { api } from '../../lib/api/endpoints.ts';
import type { ResourceCreate, ResourceListParams, ResourceUpdate } from '../../lib/api/types.ts';
import { polling } from '../../lib/polling.ts';
import { keys } from '../../lib/queryKeys.ts';

export function useResources(params: ResourceListParams) {
  return useQuery({
    queryKey: keys.resourceList(params),
    queryFn: ({ signal }) => api.listResources(params, { signal }),
    refetchInterval: polling.resources,
    // Keep the table on screen while the next page or filter loads.
    placeholderData: keepPreviousData,
  });
}

export function useResource(id: string) {
  return useQuery({
    queryKey: keys.resourceDetail(id),
    queryFn: ({ signal }) => api.getResource(id, { signal }),
    refetchInterval: polling.resources,
  });
}

/** Refresh everything a resource change can affect: lists, counts, rules, incidents. */
export function invalidateAfterResourceChange(queryClient: QueryClient, id?: string) {
  const invalidations = [
    queryClient.invalidateQueries({ queryKey: keys.resources }),
    queryClient.invalidateQueries({ queryKey: keys.overview }),
    queryClient.invalidateQueries({ queryKey: keys.rules }),
    queryClient.invalidateQueries({ queryKey: keys.incidents }),
  ];
  if (id) invalidations.push(queryClient.invalidateQueries({ queryKey: keys.resource(id) }));
  return Promise.all(invalidations);
}

export function useCreateResource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ResourceCreate) => api.createResource(body),
    onSuccess: () => invalidateAfterResourceChange(queryClient),
  });
}

export function useUpdateResource(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ResourceUpdate) => api.updateResource(id, body),
    onSuccess: () => invalidateAfterResourceChange(queryClient, id),
  });
}

export function useDeleteResource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteResource(id),
    onSuccess: async (_data, id) => {
      // The deleted resource must never be refetched (it would 404): stop anything in flight,
      // and drop its cached data once the page showing it has navigated away.
      await queryClient.cancelQueries({ queryKey: keys.resource(id) });
      window.setTimeout(() => queryClient.removeQueries({ queryKey: keys.resource(id) }), 1_000);
      return invalidateAfterResourceChange(queryClient);
    },
  });
}

export function useCheckResource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.checkResource(id),
    // Even a 409 (disabled / busy / changed) can mean the data moved on: refresh either way.
    onSettled: (_data, _error, id) => invalidateAfterResourceChange(queryClient, id),
  });
}

import type { ResourceStatus } from '../api/types.ts';

/** Sort key: the most severe status first. */
export const STATUS_ORDER: Record<ResourceStatus, number> = {
  DOWN: 0,
  DEGRADED: 1,
  UNKNOWN: 2,
  HEALTHY: 3,
};

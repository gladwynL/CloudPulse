/**
 * Polling intervals (milliseconds). Each can be overridden at build time with a VITE_POLL_*
 * variable; `0` disables that poll. TanStack Query pauses interval refetching while the browser
 * tab is hidden (we never opt into background polling), so an inactive tab generates no traffic.
 */
function interval(name: string, fallback: number): number | false {
  const raw = (import.meta.env as Record<string, string | undefined>)[name];
  if (raw === undefined || raw === '') return fallback;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : false;
}

export const polling = {
  /** System-wide counts and the service list. */
  overview: interval('VITE_POLL_OVERVIEW_MS', 15_000),
  /** Resource list, resource detail, resource rules. */
  resources: interval('VITE_POLL_RESOURCES_MS', 15_000),
  /** Incident lists and incident detail while active. */
  incidents: interval('VITE_POLL_INCIDENTS_MS', 20_000),
  /** Alert rule list (state and counters change as checks run). */
  rules: interval('VITE_POLL_RULES_MS', 20_000),
  /** Time series and summaries: aggregates change slowly, so poll less often. */
  charts: interval('VITE_POLL_CHARTS_MS', 60_000),
  /** Check history table (first page only). */
  history: interval('VITE_POLL_HISTORY_MS', 30_000),
  /** API connectivity indicator. */
  system: interval('VITE_POLL_SYSTEM_MS', 15_000),
} as const;

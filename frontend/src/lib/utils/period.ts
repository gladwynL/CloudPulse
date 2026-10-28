export type PeriodId = '1h' | '24h' | '7d' | '30d';

export interface Period {
  id: PeriodId;
  label: string;
  ms: number;
}

const HOUR = 3_600_000;

/** The backend allows windows up to METRICS_MAX_QUERY_DAYS (30 by default). */
export const PERIODS: readonly Period[] = [
  { id: '1h', label: 'Last hour', ms: HOUR },
  { id: '24h', label: 'Last 24 hours', ms: 24 * HOUR },
  { id: '7d', label: 'Last 7 days', ms: 7 * 24 * HOUR },
  { id: '30d', label: 'Last 30 days', ms: 30 * 24 * HOUR },
];

export const DEFAULT_PERIOD: PeriodId = '24h';

export function parsePeriod(value: string | null): PeriodId {
  return PERIODS.some((p) => p.id === value) ? (value as PeriodId) : DEFAULT_PERIOD;
}

export function periodById(id: PeriodId): Period {
  return PERIODS.find((p) => p.id === id) ?? PERIODS[1]!;
}

/** The trailing window ending now. Computed at fetch time so every poll sees fresh data. */
export function windowFor(id: PeriodId, now: number = Date.now()): { start: string; end: string } {
  const period = periodById(id);
  return {
    start: new Date(now - period.ms).toISOString(),
    end: new Date(now).toISOString(),
  };
}

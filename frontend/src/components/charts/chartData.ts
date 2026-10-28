import type { SeriesInterval, Timeseries } from '../../lib/api/types.ts';

export interface ChartPoint {
  /** Bucket start, epoch milliseconds (UTC-aligned by the backend). */
  t: number;
  /** Average response time over checks that got a response; null when none did. */
  avg: number | null;
  p95: number | null;
  /** healthy / total * 100 for this bucket; null when the bucket has no checks. */
  availability: number | null;
  total: number;
  healthy: number;
  degraded: number;
  down: number;
}

export const INTERVAL_MS: Record<SeriesInterval, number> = {
  '1m': 60_000,
  '5m': 300_000,
  '15m': 900_000,
  '1h': 3_600_000,
  '1d': 86_400_000,
};

/** Hard cap so a malformed response can never make the client build an enormous array. */
const MAX_POINTS = 5_000;

/**
 * The API returns only buckets that contain checks. This expands them onto the full time grid of
 * the window, filling the gaps with *null* values (not zero), so a chart can draw a break where
 * nothing was observed. A gap means "no observations", never "the service was down".
 */
export function buildChartData(series: Timeseries): ChartPoint[] {
  const step = INTERVAL_MS[series.interval];
  const byTime = new Map(series.buckets.map((b) => [Date.parse(b.bucket_start), b]));
  const start = Math.floor(Date.parse(series.window.start) / step) * step;
  const end = Date.parse(series.window.end);
  const points: ChartPoint[] = [];
  for (let t = start; t < end && points.length < MAX_POINTS; t += step) {
    const bucket = byTime.get(t);
    points.push(
      bucket
        ? {
            t,
            avg: bucket.avg_response_time_ms,
            p95: bucket.p95_response_time_ms,
            availability: bucket.availability_percent,
            total: bucket.total,
            healthy: bucket.healthy,
            degraded: bucket.degraded,
            down: bucket.down,
          }
        : {
            t,
            avg: null,
            p95: null,
            availability: null,
            total: 0,
            healthy: 0,
            degraded: 0,
            down: 0,
          },
    );
  }
  return points;
}

export function hasLatency(points: ChartPoint[]): boolean {
  return points.some((p) => p.avg !== null);
}

export function hasChecks(points: ChartPoint[]): boolean {
  return points.some((p) => p.total > 0);
}

/** Axis labels: time only for short windows, date + time for longer ones. */
export function tickFormatter(windowMs: number): (t: number) => string {
  const dayWindow = windowMs <= 36 * 3_600_000;
  const formatter = new Intl.DateTimeFormat(
    undefined,
    dayWindow
      ? { hour: '2-digit', minute: '2-digit' }
      : { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' },
  );
  return (t) => formatter.format(new Date(t));
}

export const tooltipTime = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

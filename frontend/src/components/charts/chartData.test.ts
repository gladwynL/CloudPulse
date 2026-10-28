import { describe, expect, it } from 'vitest';
import { makeTimeseries } from '../../test/fixtures.ts';
import type { TimeseriesBucket } from '../../lib/api/types.ts';
import { buildChartData, hasChecks, hasLatency, INTERVAL_MS } from './chartData.ts';

const bucket = (start: string, overrides: Partial<TimeseriesBucket> = {}): TimeseriesBucket => ({
  bucket_start: start,
  total: 5,
  healthy: 5,
  degraded: 0,
  down: 0,
  availability_percent: 100,
  avg_response_time_ms: 20,
  p95_response_time_ms: 30,
  ...overrides,
});

describe('buildChartData', () => {
  it('lays buckets on the full time grid of the window', () => {
    const points = buildChartData(
      makeTimeseries({
        window: { start: '2026-10-28T11:00:00Z', end: '2026-10-28T12:00:00Z' },
        interval: '15m',
        buckets: [bucket('2026-10-28T11:15:00Z')],
      }),
    );
    expect(points).toHaveLength(4);
    expect(points.map((p) => p.t)).toEqual([
      Date.parse('2026-10-28T11:00:00Z'),
      Date.parse('2026-10-28T11:15:00Z'),
      Date.parse('2026-10-28T11:30:00Z'),
      Date.parse('2026-10-28T11:45:00Z'),
    ]);
  });

  it('fills gaps with null, never zero, so a chart shows no data rather than downtime', () => {
    const points = buildChartData(
      makeTimeseries({
        window: { start: '2026-10-28T11:00:00Z', end: '2026-10-28T11:30:00Z' },
        interval: '15m',
        buckets: [bucket('2026-10-28T11:00:00Z')],
      }),
    );
    const gap = points[1]!;
    expect(gap.avg).toBeNull();
    expect(gap.p95).toBeNull();
    expect(gap.availability).toBeNull();
    expect(gap.total).toBe(0);
  });

  it('keeps null latency for a bucket that only had failed checks', () => {
    const points = buildChartData(
      makeTimeseries({
        window: { start: '2026-10-28T11:00:00Z', end: '2026-10-28T11:15:00Z' },
        interval: '15m',
        buckets: [
          bucket('2026-10-28T11:00:00Z', {
            healthy: 0,
            down: 5,
            availability_percent: 0,
            avg_response_time_ms: null,
            p95_response_time_ms: null,
          }),
        ],
      }),
    );
    expect(points[0]).toMatchObject({ avg: null, availability: 0, down: 5 });
  });

  it('aligns the first bucket to the interval even when the window starts mid-bucket', () => {
    const points = buildChartData(
      makeTimeseries({
        window: { start: '2026-10-28T11:07:00Z', end: '2026-10-28T11:30:00Z' },
        interval: '15m',
      }),
    );
    expect(points[0]!.t).toBe(Date.parse('2026-10-28T11:00:00Z'));
  });

  it('returns nothing for an empty window and caps pathological input', () => {
    expect(
      buildChartData(
        makeTimeseries({ window: { start: '2026-10-28T12:00:00Z', end: '2026-10-28T12:00:00Z' } }),
      ),
    ).toEqual([]);
    const huge = buildChartData(
      makeTimeseries({
        window: { start: '2000-01-01T00:00:00Z', end: '2026-01-01T00:00:00Z' },
        interval: '1m',
      }),
    );
    expect(huge.length).toBeLessThanOrEqual(5000);
  });
});

describe('presence helpers', () => {
  it('distinguish no checks from checks without latency', () => {
    const empty = buildChartData(makeTimeseries());
    expect(hasChecks(empty)).toBe(false);
    expect(hasLatency(empty)).toBe(false);

    const failedOnly = buildChartData(
      makeTimeseries({
        window: { start: '2026-10-28T11:55:00Z', end: '2026-10-28T12:00:00Z' },
        buckets: [
          bucket('2026-10-28T11:55:00Z', {
            avg_response_time_ms: null,
            p95_response_time_ms: null,
            healthy: 0,
            down: 5,
          }),
        ],
      }),
    );
    expect(hasChecks(failedOnly)).toBe(true);
    expect(hasLatency(failedOnly)).toBe(false);
  });

  it('knows every interval width', () => {
    expect(INTERVAL_MS['1m']).toBe(60_000);
    expect(INTERVAL_MS['1d']).toBe(86_400_000);
  });
});

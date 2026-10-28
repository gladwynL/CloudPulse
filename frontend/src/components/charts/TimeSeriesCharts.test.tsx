import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { TimeseriesBucket } from '../../lib/api/types.ts';
import { makeTimeseries } from '../../test/fixtures.ts';
import { AvailabilityChart, LatencyChart } from './TimeSeriesCharts.tsx';

const bucket = (overrides: Partial<TimeseriesBucket> = {}): TimeseriesBucket => ({
  bucket_start: '2026-10-28T11:55:00Z',
  total: 5,
  healthy: 5,
  degraded: 0,
  down: 0,
  availability_percent: 100,
  avg_response_time_ms: 20,
  p95_response_time_ms: 30,
  ...overrides,
});

describe('chart empty states', () => {
  it('says there are no checks rather than drawing an empty or flat chart', () => {
    const series = makeTimeseries({ buckets: [] });
    render(
      <>
        <LatencyChart series={series} />
        <AvailabilityChart series={series} />
      </>,
    );
    expect(screen.getAllByText('No checks in this period.')).toHaveLength(2);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('explains when checks ran but none got a response, instead of plotting zero latency', () => {
    const series = makeTimeseries({
      buckets: [
        bucket({
          healthy: 0,
          down: 5,
          availability_percent: 0,
          avg_response_time_ms: null,
          p95_response_time_ms: null,
        }),
      ],
    });
    render(<LatencyChart series={series} />);
    expect(screen.getByRole('status')).toHaveTextContent(/no latency to plot/i);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });
});

describe('charts with data', () => {
  const series = makeTimeseries({ buckets: [bucket()] });

  it('exposes a text alternative for the latency chart', () => {
    render(<LatencyChart series={series} />);
    expect(
      screen.getByRole('img', { name: /Response time over time, 1 buckets with data/ }),
    ).toBeInTheDocument();
  });

  it('exposes a text alternative for the availability chart', () => {
    render(<AvailabilityChart series={series} />);
    expect(
      screen.getByRole('img', { name: /Availability percentage over time, 1 buckets with data/ }),
    ).toBeInTheDocument();
  });

  it('draws availability for a bucket of only failed checks, because 0% is real data', () => {
    render(
      <AvailabilityChart
        series={makeTimeseries({
          buckets: [
            bucket({ healthy: 0, down: 5, availability_percent: 0, avg_response_time_ms: null }),
          ],
        })}
      />,
    );
    expect(screen.getByRole('img', { name: /Availability percentage/ })).toBeInTheDocument();
  });
});

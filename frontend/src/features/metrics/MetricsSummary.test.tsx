import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { makeSummary } from '../../test/fixtures.ts';
import { MetricsSummary } from './MetricsSummary.tsx';

describe('MetricsSummary', () => {
  it('shows availability, check counts and latency statistics', () => {
    render(<MetricsSummary summary={makeSummary()} />);
    expect(screen.getByText('90%')).toBeInTheDocument();
    expect(screen.getByText('90 of 100 checks healthy')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.getByText('55.0 ms'.replace('55.0', '55.0'))).toBeInTheDocument();
    expect(screen.getByText('96 responses')).toBeInTheDocument();
    expect(screen.getByText('210 ms')).toBeInTheDocument();
  });

  it('labels availability as observation-based, not uptime', () => {
    render(<MetricsSummary summary={makeSummary()} />);
    expect(screen.getByText(/not continuous uptime/i)).toBeInTheDocument();
  });

  it('shows unknown, not 0% or 100%, when no checks ran', () => {
    render(
      <MetricsSummary
        summary={makeSummary({
          checks: { total: 0, healthy: 0, degraded: 0, down: 0 },
          availability_percent: null,
          latency: {
            samples: 0,
            avg_ms: null,
            min_ms: null,
            max_ms: null,
            p50_ms: null,
            p95_ms: null,
          },
          first_checked_at: null,
          last_checked_at: null,
        })}
      />,
    );
    const availability = screen.getByText('Availability').closest('.stat') as HTMLElement;
    expect(within(availability).getByText('—')).toBeInTheDocument();
    expect(within(availability).getByText('no checks in this period')).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
  });

  it('shows no latency for a period where every check failed to respond', () => {
    render(
      <MetricsSummary
        summary={makeSummary({
          checks: { total: 10, healthy: 0, degraded: 0, down: 10 },
          availability_percent: 0,
          latency: {
            samples: 0,
            avg_ms: null,
            min_ms: null,
            max_ms: null,
            p50_ms: null,
            p95_ms: null,
          },
        })}
      />,
    );
    expect(screen.getByText('0%')).toBeInTheDocument();
    expect(screen.getByText('0 responses')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(5);
  });
});

import type { MetricsSummary as Summary } from '../../lib/api/types.ts';
import { formatCount, formatMs, formatPercent } from '../../lib/utils/format.ts';

function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="stat" style={tone ? { ['--tone' as string]: `var(--${tone})` } : undefined}>
      <span className="stat__label">{label}</span>
      <span className="stat__value">{value}</span>
      {hint && <span className="stat__hint">{hint}</span>}
    </div>
  );
}

/**
 * Availability here is **observation-based**: healthy checks divided by all checks in the period.
 * It is not continuously measured uptime, and nothing is assumed about the time between checks.
 */
export function MetricsSummary({ summary }: { summary: Summary }) {
  const { checks, latency } = summary;
  const none = checks.total === 0;
  return (
    <div className="stack">
      <div className="grid-stats">
        <Metric
          label="Availability"
          value={formatPercent(summary.availability_percent)}
          tone={
            summary.availability_percent === null
              ? undefined
              : summary.availability_percent >= 99
                ? 'healthy'
                : summary.availability_percent >= 90
                  ? 'degraded'
                  : 'down'
          }
          hint={
            none
              ? 'no checks in this period'
              : `${checks.healthy} of ${checks.total} checks healthy`
          }
        />
        <Metric label="Total checks" value={formatCount(checks.total)} />
        <Metric label="Healthy" value={formatCount(checks.healthy)} tone="healthy" />
        <Metric label="Degraded" value={formatCount(checks.degraded)} tone="degraded" />
        <Metric label="Down" value={formatCount(checks.down)} tone="down" />
      </div>
      <div className="grid-stats">
        <Metric
          label="Average latency"
          value={formatMs(latency.avg_ms)}
          hint={`${formatCount(latency.samples)} responses`}
        />
        <Metric label="p50" value={formatMs(latency.p50_ms)} />
        <Metric label="p95" value={formatMs(latency.p95_ms)} />
        <Metric label="Min" value={formatMs(latency.min_ms)} />
        <Metric label="Max" value={formatMs(latency.max_ms)} />
      </div>
      <p className="chart-note">
        Availability counts a check as healthy only when it returned the expected status; degraded
        responses do not count. It reflects the checks that ran, not continuous uptime. Latency
        statistics cover checks that received a response; checks with no response (down) have none.
      </p>
    </div>
  );
}

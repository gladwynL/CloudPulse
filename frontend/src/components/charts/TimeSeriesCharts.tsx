import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Timeseries } from '../../lib/api/types.ts';
import { formatMs, formatPercent } from '../../lib/utils/format.ts';
import {
  INTERVAL_MS,
  buildChartData,
  hasChecks,
  hasLatency,
  tickFormatter,
  tooltipTime,
  type ChartPoint,
} from './chartData.ts';

const AXIS = { stroke: 'var(--border-strong)' };

function TooltipBody({
  active,
  payload,
  intervalMs,
  kind,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload: ChartPoint }>;
  intervalMs: number;
  kind: 'latency' | 'availability';
}) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;
  return (
    <div className="chart-tooltip">
      <p className="chart-tooltip__time">
        {tooltipTime.format(new Date(point.t))} · {Math.round(intervalMs / 60_000)} min bucket
      </p>
      {point.total === 0 ? (
        <p>No checks in this period (no data, not downtime)</p>
      ) : (
        <dl>
          {kind === 'latency' ? (
            <>
              <dt>Average</dt>
              <dd>{point.avg === null ? 'no response' : formatMs(point.avg)}</dd>
              <dt>p95</dt>
              <dd>{point.p95 === null ? 'no response' : formatMs(point.p95)}</dd>
            </>
          ) : (
            <>
              <dt>Availability</dt>
              <dd>{formatPercent(point.availability)}</dd>
            </>
          )}
          <dt>Checks</dt>
          <dd>{point.total}</dd>
          <dt>Healthy / degraded / down</dt>
          <dd>
            {point.healthy} / {point.degraded} / {point.down}
          </dd>
        </dl>
      )}
    </div>
  );
}

function axisProps(series: Timeseries) {
  const start =
    Math.floor(Date.parse(series.window.start) / INTERVAL_MS[series.interval]) *
    INTERVAL_MS[series.interval];
  const end = Date.parse(series.window.end);
  return { domain: [start, end] as [number, number], format: tickFormatter(end - start) };
}

/** Average and p95 response time. Buckets without a response are gaps, not zeros. */
export function LatencyChart({ series }: { series: Timeseries }) {
  const points = buildChartData(series);
  const { domain, format } = axisProps(series);
  const intervalMs = INTERVAL_MS[series.interval];
  const sparse = points.filter((p) => p.avg !== null).length <= 80;
  if (!hasLatency(points)) {
    return (
      <p className="muted" role="status">
        {hasChecks(points)
          ? 'No check in this period received a response, so there is no latency to plot.'
          : 'No checks in this period.'}
      </p>
    );
  }
  return (
    <div
      className="chart"
      role="img"
      aria-label={`Response time over time, ${points.filter((p) => p.avg !== null).length} buckets with data, in milliseconds`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={domain}
            tickFormatter={format}
            tickCount={6}
            {...AXIS}
          />
          <YAxis width={56} tickFormatter={(v: number) => formatMs(v)} {...AXIS} />
          <Tooltip
            content={(p) => (
              <TooltipBody
                active={p.active}
                payload={p.payload as never}
                intervalMs={intervalMs}
                kind="latency"
              />
            )}
          />
          <Legend verticalAlign="top" height={28} />
          <Line
            name="Average (ms)"
            dataKey="avg"
            stroke="var(--series-latency)"
            strokeWidth={2}
            dot={sparse}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            name="p95 (ms)"
            dataKey="p95"
            stroke="var(--series-p95)"
            strokeWidth={1.5}
            strokeDasharray="5 4"
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Check-based availability per bucket (0 to 100%). Empty buckets are gaps, not 0% or 100%. */
export function AvailabilityChart({ series }: { series: Timeseries }) {
  const points = buildChartData(series);
  const { domain, format } = axisProps(series);
  const intervalMs = INTERVAL_MS[series.interval];
  if (!hasChecks(points)) {
    return (
      <p className="muted" role="status">
        No checks in this period.
      </p>
    );
  }
  return (
    <div
      className="chart"
      role="img"
      aria-label={`Availability percentage over time, ${points.filter((p) => p.total > 0).length} buckets with data`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={domain}
            tickFormatter={format}
            tickCount={6}
            {...AXIS}
          />
          <YAxis
            width={56}
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            tickFormatter={(v: number) => `${v}%`}
            {...AXIS}
          />
          <Tooltip
            content={(p) => (
              <TooltipBody
                active={p.active}
                payload={p.payload as never}
                intervalMs={intervalMs}
                kind="availability"
              />
            )}
          />
          <Area
            name="Availability (%)"
            dataKey="availability"
            type="linear"
            stroke="var(--series-availability)"
            fill="var(--series-availability)"
            fillOpacity={0.14}
            strokeWidth={2}
            dot={points.filter((p) => p.total > 0).length <= 80}
            connectNulls={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

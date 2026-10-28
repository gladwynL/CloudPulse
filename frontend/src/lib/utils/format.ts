const DASH = '—';

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`;
  return `${value < 100 ? value.toFixed(1) : Math.round(value)} ms`;
}

/** Percentages show at most two decimals without trailing zeros (99.5%, 100%, 62.5%). */
export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return `${Number(value.toFixed(2))}%`;
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

export function formatInterval(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) {
    const minutes = seconds / 60;
    return Number.isInteger(minutes) ? `${minutes} min` : `${seconds}s`;
  }
  const hours = seconds / 3600;
  return Number.isInteger(hours) ? `${hours} h` : `${Math.round(seconds / 60)} min`;
}

const dateTime = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

/** Local, human-readable date and time. The exact UTC instant is available via `utcTitle`. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? DASH : dateTime.format(date);
}

export function utcTitle(iso: string | null | undefined): string | undefined {
  if (!iso) return undefined;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? undefined : `${date.toISOString()} (UTC)`;
}

export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return DASH;
  const seconds = Math.round((now - then) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 5) return 'just now';
  const unit =
    abs < 60
      ? `${abs}s`
      : abs < 3600
        ? `${Math.floor(abs / 60)}m`
        : abs < 86_400
          ? `${Math.floor(abs / 3600)}h`
          : `${Math.floor(abs / 86_400)}d`;
  return seconds >= 0 ? `${unit} ago` : `in ${unit}`;
}

/** Duration between two instants, e.g. "4m 12s". */
export function formatDuration(fromIso: string, toIso: string): string {
  const diff = new Date(toIso).getTime() - new Date(fromIso).getTime();
  const seconds = Math.max(0, Math.round(diff / 1000));
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

/**
 * A monitoring target for display: host and path only. Query strings (which can carry tokens)
 * and fragments are never shown; their presence is indicated with an ellipsis.
 */
export function displayTarget(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === '/' ? '' : parsed.pathname;
    return `${parsed.host}${path}${parsed.search ? '?…' : ''}`;
  } catch {
    return 'invalid URL';
  }
}

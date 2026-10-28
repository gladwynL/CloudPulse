import { formatDateTime, formatRelative, utcTitle } from '../../lib/utils/format.ts';

/** "12s ago" with the exact local time on hover/focus-less screens via the title attribute. */
export function RelativeTime({
  iso,
  empty = 'never',
}: {
  iso: string | null | undefined;
  empty?: string;
}) {
  if (!iso) return <span className="subtle">{empty}</span>;
  return (
    <time dateTime={iso} title={`${formatDateTime(iso)} · ${utcTitle(iso)}`}>
      {formatRelative(iso)}
    </time>
  );
}

export function DateTime({ iso, empty = '—' }: { iso: string | null | undefined; empty?: string }) {
  if (!iso) return <span className="subtle">{empty}</span>;
  return (
    <time dateTime={iso} title={utcTitle(iso)}>
      {formatDateTime(iso)}
    </time>
  );
}

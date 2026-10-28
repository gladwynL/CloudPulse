import { ChevronLeft, ChevronRight } from 'lucide-react';
import { formatCount } from '../../lib/utils/format.ts';
import { Button } from './Button.tsx';

/** Offset pagination (resources and rules), where the API reports a total. */
export function OffsetPager({
  offset,
  limit,
  total,
  noun,
  onChange,
}: {
  offset: number;
  limit: number;
  total: number;
  noun: string;
  onChange: (offset: number) => void;
}) {
  if (total === 0) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <nav className="pager" aria-label={`${noun} pagination`}>
      <span>
        Showing {formatCount(from)}–{formatCount(to)} of {formatCount(total)} {noun}
      </span>
      <div className="row">
        <Button small disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          <ChevronLeft size={14} aria-hidden="true" /> Previous
        </Button>
        <Button small disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          Next <ChevronRight size={14} aria-hidden="true" />
        </Button>
      </div>
    </nav>
  );
}

/** Cursor pagination (history and incidents): the API gives a cursor, never a total. */
export function LoadMore({
  shown,
  noun,
  hasMore,
  loading,
  onLoadMore,
}: {
  shown: number;
  noun: string;
  hasMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
}) {
  return (
    <div className="pager">
      <span>
        {formatCount(shown)} {noun} loaded{hasMore ? '' : ' (end of list)'}
      </span>
      {hasMore && (
        <Button small onClick={onLoadMore} loading={loading}>
          Load older
        </Button>
      )}
    </div>
  );
}

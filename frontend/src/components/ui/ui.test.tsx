import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/api/client.ts';
import { renderWithProviders } from '../../test/render.tsx';
import {
  AlertStateBadge,
  EnabledBadge,
  IncidentStatusBadge,
  SeverityBadge,
  StatusBadge,
} from './Badges.tsx';
import { ConfirmDialog, Modal } from './Dialog.tsx';
import { LoadMore, OffsetPager } from './Pager.tsx';
import { EmptyState, ErrorState, LoadingState, Notice } from './States.tsx';
import { useToast } from './useToast.ts';

describe('status badges', () => {
  it('always carry a text label, not just a colour', () => {
    render(
      <>
        <StatusBadge status="HEALTHY" />
        <StatusBadge status="DEGRADED" />
        <StatusBadge status="DOWN" />
        <StatusBadge status="UNKNOWN" />
        <AlertStateBadge state="FIRING" />
        <AlertStateBadge state="OK" />
        <AlertStateBadge state="INSUFFICIENT_DATA" />
        <IncidentStatusBadge status="OPEN" />
        <IncidentStatusBadge status="ACKNOWLEDGED" />
        <IncidentStatusBadge status="RESOLVED" />
        <SeverityBadge severity="CRITICAL" />
        <SeverityBadge severity="WARNING" />
        <EnabledBadge enabled />
        <EnabledBadge enabled={false} />
      </>,
    );
    for (const label of [
      'Healthy',
      'Degraded',
      'Down',
      'Unknown',
      'Firing',
      'OK',
      'Insufficient data',
      'Open',
      'Acknowledged',
      'Resolved',
      'Critical',
      'Warning',
      'Enabled',
      'Disabled',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it('pairs each label with a decorative icon hidden from assistive technology', () => {
    const { container } = render(<StatusBadge status="DOWN" />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
  });

  it('marks unknown and disabled as visually distinct (dashed) beyond colour', () => {
    const { container } = render(
      <>
        <StatusBadge status="UNKNOWN" />
        <EnabledBadge enabled={false} />
        <StatusBadge status="HEALTHY" />
      </>,
    );
    expect(container.querySelectorAll('.badge--dashed')).toHaveLength(2);
  });
});

describe('state components', () => {
  it('announces loading politely', () => {
    render(<LoadingState label="Loading things…" />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading things…');
  });

  it('shows an empty state with its explanation and action', () => {
    render(
      <EmptyState title="Nothing here" action={<button>Do it</button>}>
        Explanation text
      </EmptyState>,
    );
    expect(screen.getByText('Nothing here')).toBeInTheDocument();
    expect(screen.getByText('Explanation text')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Do it' })).toBeInTheDocument();
  });

  it.each([
    ['network', 'Cannot reach the API'],
    ['timeout', 'The API is not responding'],
    ['unavailable', 'Service unavailable'],
    ['server', 'Server error'],
    ['not_found', 'Not found'],
  ] as const)('titles a %s error accurately', (kind, title) => {
    render(<ErrorState error={new ApiError(kind, 'details here')} />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(title);
    expect(alert).toHaveTextContent('details here');
  });

  it('offers retry only when a handler exists, and calls it', async () => {
    const retry = vi.fn();
    const { rerender } = render(<ErrorState error={new Error('x')} />);
    expect(screen.queryByRole('button', { name: /try again/i })).not.toBeInTheDocument();
    rerender(<ErrorState error={new Error('x')} onRetry={retry} />);
    await userEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it('exposes error notices to assistive technology but not info notices', () => {
    render(
      <>
        <Notice tone="error" title="Bad">
          broken
        </Notice>
        <Notice tone="info">fyi</Notice>
      </>,
    );
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.getByRole('alert')).toHaveTextContent('broken');
  });
});

describe('pagination controls', () => {
  it('shows the visible range and disables Previous on the first page', () => {
    render(<OffsetPager offset={0} limit={25} total={60} noun="resources" onChange={() => {}} />);
    expect(screen.getByText(/Showing 1–25 of 60 resources/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /next/i })).toBeEnabled();
  });

  it('disables Next on the last page and clamps the last range', async () => {
    const onChange = vi.fn();
    render(<OffsetPager offset={50} limit={25} total={60} noun="resources" onChange={onChange} />);
    expect(screen.getByText(/Showing 51–60 of 60/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /next/i })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /previous/i }));
    expect(onChange).toHaveBeenCalledWith(25);
  });

  it('renders nothing for an empty list', () => {
    const { container } = render(
      <OffsetPager offset={0} limit={25} total={0} noun="x" onChange={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('describes cursor pagination without inventing a total', async () => {
    const onLoadMore = vi.fn();
    const { rerender } = render(
      <LoadMore shown={25} noun="checks" hasMore loading={false} onLoadMore={onLoadMore} />,
    );
    expect(screen.getByText('25 checks loaded')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /load older/i }));
    expect(onLoadMore).toHaveBeenCalledOnce();

    rerender(
      <LoadMore shown={30} noun="checks" hasMore={false} loading={false} onLoadMore={onLoadMore} />,
    );
    expect(screen.getByText('30 checks loaded (end of list)')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('Modal and ConfirmDialog', () => {
  function Harness({ onConfirm }: { onConfirm: () => void }) {
    const [open, setOpen] = useState(true);
    return (
      <>
        <button onClick={() => setOpen(true)}>Open</button>
        <ConfirmDialog
          open={open}
          title="Delete it?"
          confirmLabel="Yes, delete"
          danger
          onConfirm={onConfirm}
          onCancel={() => setOpen(false)}
        >
          <p>Are you sure?</p>
        </ConfirmDialog>
      </>
    );
  }

  it('is a labelled dialog with an accessible title and content', () => {
    render(<Harness onConfirm={() => {}} />);
    const dialog = screen.getByRole('dialog', { name: 'Delete it?' });
    expect(within(dialog).getByText('Are you sure?')).toBeInTheDocument();
  });

  it('confirms and cancels through real buttons', async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, delete' }));
    expect(onConfirm).toHaveBeenCalledOnce();

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes from the close button and does not render content while closed', async () => {
    render(<Harness onConfirm={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(screen.queryByText('Are you sure?')).not.toBeInTheDocument();
  });

  it('treats the Escape/cancel event as a request to close, not a silent dismissal', () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Hello">
        body
      </Modal>,
    );
    const dialog = document.querySelector('dialog') as HTMLDialogElement;
    const cancel = new Event('cancel', { cancelable: true });
    act(() => {
      dialog.dispatchEvent(cancel);
    });
    expect(cancel.defaultPrevented).toBe(true);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('blocks dismissal while a confirmed action is pending and shows progress', async () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Working"
        confirmLabel="Go"
        pending
        onConfirm={() => {}}
        onCancel={onCancel}
      >
        body
      </ConfirmDialog>,
    );
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Go' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Go' })).toHaveAttribute('aria-busy', 'true');
    const dialog = document.querySelector('dialog') as HTMLDialogElement;
    act(() => {
      dialog.dispatchEvent(new Event('cancel', { cancelable: true }));
    });
    expect(onCancel).not.toHaveBeenCalled();
  });
});

describe('toasts', () => {
  function Trigger() {
    const toast = useToast();
    return (
      <>
        <button onClick={() => toast.success('Saved it')}>ok</button>
        <button onClick={() => toast.error('Broke it')}>bad</button>
      </>
    );
  }

  it('announces messages in a polite live region and can be dismissed', async () => {
    renderWithProviders(<Trigger />);
    await userEvent.click(screen.getByRole('button', { name: 'ok' }));
    await userEvent.click(screen.getByRole('button', { name: 'bad' }));

    const region = screen.getByRole('region', { name: 'Notifications' });
    expect(region).toHaveAttribute('aria-live', 'polite');
    expect(within(region).getByText('Saved it')).toBeInTheDocument();
    expect(within(region).getByText('Broke it')).toBeInTheDocument();

    await userEvent.click(
      within(region).getAllByRole('button', { name: 'Dismiss notification' })[0]!,
    );
    expect(within(region).queryByText('Saved it')).not.toBeInTheDocument();
  });

  it('refuses to be used outside its provider', () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => render(<Trigger />)).toThrow(/ToastProvider/);
    log.mockRestore();
  });
});

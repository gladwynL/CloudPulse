import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { makeIncident, makeResource } from '../../test/fixtures.ts';
import { installMockApi, type MockApi } from '../../test/mockApi.ts';
import { renderWithProviders } from '../../test/render.tsx';
import { IncidentsPage } from './IncidentsPage.tsx';

const resources = { items: [makeResource()], total: 1, limit: 200, offset: 0 };

function api(): MockApi {
  return installMockApi().get('/api/v1/resources', resources);
}

describe('IncidentsPage', () => {
  it('lists active incidents by default using the server-side active filter', async () => {
    const mock = api().get('/api/v1/incidents', {
      items: [
        makeIncident({ id: 'i1', title: 'First incident', severity: 'CRITICAL' }),
        makeIncident({ id: 'i2', title: 'Second incident', status: 'ACKNOWLEDGED' }),
      ],
      limit: 25,
      next_cursor: null,
    });
    renderWithProviders(<IncidentsPage />);

    expect(await screen.findByRole('link', { name: 'First incident' })).toHaveAttribute(
      'href',
      '/incidents/i1',
    );
    const table = within(screen.getByRole('table'));
    expect(table.getByRole('row', { name: /First incident.*Critical/ })).toBeInTheDocument();
    expect(table.getByRole('row', { name: /Second incident.*Acknowledged/ })).toBeInTheDocument();
    expect(mock.callsTo('GET', '/api/v1/incidents')[0]?.query.get('active')).toBe('true');
  });

  it('says so when everything is fine', async () => {
    api().get('/api/v1/incidents', { items: [], limit: 25, next_cursor: null });
    renderWithProviders(<IncidentsPage />);
    expect(await screen.findByText('No active incidents')).toBeInTheDocument();
  });

  it('filters by status, severity and resource', async () => {
    const mock = api().get('/api/v1/incidents', { items: [], limit: 25, next_cursor: null });
    renderWithProviders(<IncidentsPage />);
    await screen.findByText('No active incidents');

    await userEvent.selectOptions(screen.getByLabelText('Show'), 'RESOLVED');
    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'CRITICAL');
    await userEvent.selectOptions(
      await screen
        .findByRole('option', { name: 'Checkout API' })
        .then(() => screen.getByLabelText('Resource')),
      'res-1',
    );

    await waitFor(() => {
      const last = mock.callsTo('GET', '/api/v1/incidents').at(-1);
      expect(last?.query.get('status')).toBe('RESOLVED');
      expect(last?.query.has('active')).toBe(false);
      expect(last?.query.get('severity')).toBe('CRITICAL');
      expect(last?.query.get('resource_id')).toBe('res-1');
    });
    expect(await screen.findByText('No incidents match these filters')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /reset filters/i }));
    expect(await screen.findByText('No active incidents')).toBeInTheDocument();
  });

  it('reads its filters from the URL so views can be linked', async () => {
    const mock = api().get('/api/v1/incidents', { items: [], limit: 25, next_cursor: null });
    renderWithProviders(<IncidentsPage />, { route: '/incidents?state=all&severity=WARNING' });
    await screen.findByText('No incidents match these filters');
    const call = mock.callsTo('GET', '/api/v1/incidents')[0];
    expect(call?.query.has('active')).toBe(false);
    expect(call?.query.has('status')).toBe(false);
    expect(call?.query.get('severity')).toBe('WARNING');
  });

  it('loads more with the cursor and appends to the list', async () => {
    const mock = api().on('GET', '/api/v1/incidents', ({ query }) => {
      const cursor = query.get('cursor');
      return {
        body: cursor
          ? {
              items: [makeIncident({ id: 'i2', title: 'Older incident' })],
              limit: 25,
              next_cursor: null,
            }
          : {
              items: [makeIncident({ id: 'i1', title: 'Newer incident' })],
              limit: 25,
              next_cursor: 'cursor-2',
            },
      };
    });
    renderWithProviders(<IncidentsPage />);
    await screen.findByText('Newer incident');

    await userEvent.click(screen.getByRole('button', { name: /load older/i }));
    expect(await screen.findByText('Older incident')).toBeInTheDocument();
    // The first page is kept, and the cursor was sent unchanged.
    expect(screen.getByText('Newer incident')).toBeInTheDocument();
    expect(mock.callsTo('GET', '/api/v1/incidents').at(-1)?.query.get('cursor')).toBe('cursor-2');
    expect(screen.queryByRole('button', { name: /load older/i })).not.toBeInTheDocument();
  });

  it('keeps showing loaded incidents and offers retry when a later request fails', async () => {
    api().on('GET', '/api/v1/incidents', ({ query }) =>
      query.get('cursor')
        ? { status: 503, body: { detail: 'down' } }
        : {
            body: {
              items: [makeIncident({ title: 'Kept incident' })],
              limit: 25,
              next_cursor: 'c2',
            },
          },
    );
    renderWithProviders(<IncidentsPage />);
    await screen.findByText('Kept incident');
    await userEvent.click(screen.getByRole('button', { name: /load older/i }));
    await waitFor(() => expect(screen.getByRole('button', { name: /load older/i })).toBeEnabled());
    expect(screen.getByText('Kept incident')).toBeInTheDocument();
  });

  it('shows an error state with retry when the first request fails', async () => {
    api().get('/api/v1/incidents', { detail: 'down' }, 503);
    renderWithProviders(<IncidentsPage />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Service unavailable');
    expect(within(alert).getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });
});

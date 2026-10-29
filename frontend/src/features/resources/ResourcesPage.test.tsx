import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { makeResource } from '../../test/fixtures.ts';
import { installMockApi } from '../../test/mockApi.ts';
import { renderWithProviders } from '../../test/render.tsx';
import { ResourcesPage } from './ResourcesPage.tsx';
import { expectCellsLabelledByHeaders } from '../../test/tableLabels.ts';

const page = (items = [makeResource()], total = items.length, offset = 0) => ({
  items,
  total,
  limit: 25,
  offset,
});

describe('ResourcesPage list', () => {
  it('shows a loading state, then the resources with status text and a safe target', async () => {
    const mock = installMockApi().get(
      '/api/v1/resources',
      page([
        makeResource({
          url: 'https://api.example.com/health?token=SECRET',
          description: 'Payments',
        }),
        makeResource({
          id: 'res-2',
          name: 'Search',
          current_status: 'DOWN',
          last_response_time_ms: null,
        }),
      ]),
    );
    renderWithProviders(<ResourcesPage />);

    expect(screen.getByText(/loading resources/i)).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'Checkout API' })).toHaveAttribute(
      'href',
      '/resources/res-1',
    );
    const table = within(screen.getByRole('table', { name: 'Monitored resources' }));
    expect(table.getByText('Healthy')).toBeInTheDocument();
    expect(table.getByText('Down')).toBeInTheDocument();
    expect(table.getByText('Payments')).toBeInTheDocument();
    // The query string (which may hold a token) is never rendered.
    expect(screen.getByText('api.example.com/health?…')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('SECRET');
    expect(mock.calls[0]?.query.get('limit')).toBe('25');
  });

  it('shows an empty state with a call to action when nothing exists', async () => {
    installMockApi().get('/api/v1/resources', page([], 0));
    renderWithProviders(<ResourcesPage />);
    expect(await screen.findByText('No resources yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add your first resource/i })).toBeInTheDocument();
  });

  it('shows an actionable error and recovers on retry', async () => {
    let healthy = false;
    installMockApi().on('GET', '/api/v1/resources', () =>
      healthy ? { body: page() } : { status: 503, body: { detail: 'The database is unavailable' } },
    );
    renderWithProviders(<ResourcesPage />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Service unavailable');
    expect(alert).toHaveTextContent('The database is unavailable');

    healthy = true;
    await userEvent.click(within(alert).getByRole('button', { name: /try again/i }));
    expect(await screen.findByRole('link', { name: 'Checkout API' })).toBeInTheDocument();
  });

  it('says the backend is unreachable on a network failure', async () => {
    installMockApi().on('GET', '/api/v1/resources', 'network-error');
    renderWithProviders(<ResourcesPage />);
    expect(await screen.findByText('Cannot reach the API')).toBeInTheDocument();
  });

  it('sends the search text to the API after a short pause', async () => {
    const mock = installMockApi().get('/api/v1/resources', page());
    renderWithProviders(<ResourcesPage />);
    await screen.findByRole('link', { name: 'Checkout API' });

    await userEvent.type(screen.getByLabelText('Search by name'), 'check');
    await waitFor(() => expect(mock.calls.some((c) => c.query.get('q') === 'check')).toBe(true));
  });

  it('filters by status and offers to clear filters when nothing matches', async () => {
    const mock = installMockApi().on('GET', '/api/v1/resources', ({ query }) => ({
      body: query.get('current_status') === 'DOWN' ? page([], 0) : page(),
    }));
    renderWithProviders(<ResourcesPage />);
    await screen.findByRole('link', { name: 'Checkout API' });

    await userEvent.selectOptions(screen.getByLabelText('Status'), 'DOWN');
    expect(await screen.findByText('No resources match these filters')).toBeInTheDocument();
    expect(mock.calls.at(-1)?.query.get('current_status')).toBe('DOWN');

    await userEvent.click(screen.getByRole('button', { name: /^clear filters$/i }));
    expect(await screen.findByRole('link', { name: 'Checkout API' })).toBeInTheDocument();
  });

  it('paginates with the offset instead of loading everything', async () => {
    const mock = installMockApi().on('GET', '/api/v1/resources', ({ query }) => {
      const offset = Number(query.get('offset') ?? 0);
      return {
        body: page(
          [
            makeResource({
              id: `r${offset}`,
              name: offset ? 'Second page item' : 'First page item',
            }),
          ],
          60,
          offset,
        ),
      };
    });
    renderWithProviders(<ResourcesPage />);
    expect(await screen.findByText('First page item')).toBeInTheDocument();
    expect(screen.getByText(/showing 1.{1,3}25 of 60/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(await screen.findByText('Second page item')).toBeInTheDocument();
    expect(mock.calls.at(-1)?.query.get('offset')).toBe('25');
  });
});

describe('ResourcesPage create', () => {
  async function openForm() {
    await screen.findByRole('link', { name: 'Checkout API' });
    await userEvent.click(screen.getByRole('button', { name: /add resource/i }));
    return await screen.findByRole('dialog');
  }

  it('validates before sending anything', async () => {
    const mock = installMockApi().get('/api/v1/resources', page());
    renderWithProviders(<ResourcesPage />);
    const dialog = await openForm();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Add resource' }));

    expect(within(dialog).getByText('Enter a name.')).toBeInTheDocument();
    expect(within(dialog).getByText('Enter the URL to check.')).toBeInTheDocument();
    expect(mock.callsTo('POST', '/api/v1/resources')).toHaveLength(0);
  });

  it('creates a resource with trimmed, typed values and reports success', async () => {
    const mock = installMockApi()
      .get('/api/v1/resources', page())
      .on('POST', '/api/v1/resources', ({ body }) => ({
        status: 201,
        body: makeResource({ id: 'new-1', name: (body as { name: string }).name }),
      }));
    renderWithProviders(<ResourcesPage />);
    const dialog = await openForm();

    await userEvent.type(within(dialog).getByLabelText(/^Name/), '  Billing  ');
    await userEvent.type(
      within(dialog).getByLabelText(/^URL to check/),
      'https://billing.example.com/ping',
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add resource' }));

    await waitFor(() => expect(mock.callsTo('POST', '/api/v1/resources')).toHaveLength(1));
    expect(mock.callsTo('POST', '/api/v1/resources')[0]?.body).toMatchObject({
      name: 'Billing',
      url: 'https://billing.example.com/ping',
      check_interval_seconds: 60,
      timeout_seconds: 10,
      expected_status_code: 200,
      is_enabled: true,
    });
    expect(await screen.findByText(/Added “Billing”/)).toBeInTheDocument();
  });

  it('shows the backend’s validation message next to the field (422)', async () => {
    installMockApi()
      .get('/api/v1/resources', page())
      .on('POST', '/api/v1/resources', {
        status: 422,
        body: { detail: [{ loc: ['body', 'url'], msg: 'Target host is not allowed' }] },
      });
    renderWithProviders(<ResourcesPage />);
    const dialog = await openForm();

    await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Internal');
    await userEvent.type(within(dialog).getByLabelText(/^URL to check/), 'http://169.254.169.254/');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add resource' }));

    expect(await within(dialog).findByText('Target host is not allowed')).toBeInTheDocument();
    expect(dialog).toBeVisible();
  });

  it('keeps the dialog open and explains when the API is down (503)', async () => {
    installMockApi()
      .get('/api/v1/resources', page())
      .on('POST', '/api/v1/resources', { status: 503, body: { detail: 'Database unavailable' } });
    renderWithProviders(<ResourcesPage />);
    const dialog = await openForm();

    await userEvent.type(within(dialog).getByLabelText(/^Name/), 'Billing');
    await userEvent.type(
      within(dialog).getByLabelText(/^URL to check/),
      'https://billing.example.com',
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add resource' }));

    expect(await within(dialog).findByText('Database unavailable')).toBeInTheDocument();
    // Values are still there so the user can retry.
    expect(within(dialog).getByLabelText(/^Name/)).toHaveValue('Billing');
  });
});

describe('ResourcesPage row actions', () => {
  it('asks for confirmation before deleting and explains what is lost and kept', async () => {
    const mock = installMockApi()
      .get('/api/v1/resources', page())
      .on('DELETE', '/api/v1/resources/res-1', { status: 204 });
    renderWithProviders(<ResourcesPage />);
    await screen.findByRole('link', { name: 'Checkout API' });

    await userEvent.click(screen.getByRole('button', { name: 'Delete Checkout API' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/all of its monitoring history/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/incidents it caused are kept/i)).toBeInTheDocument();
    expect(mock.callsTo('DELETE', '/api/v1/resources/res-1')).toHaveLength(0);

    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete resource' }));
    await waitFor(() => expect(mock.callsTo('DELETE', '/api/v1/resources/res-1')).toHaveLength(1));
    expect(await screen.findByText(/Deleted “Checkout API”/)).toBeInTheDocument();
  });

  it('does nothing when the delete is cancelled', async () => {
    const mock = installMockApi().get('/api/v1/resources', page());
    renderWithProviders(<ResourcesPage />);
    await screen.findByRole('link', { name: 'Checkout API' });

    await userEvent.click(screen.getByRole('button', { name: 'Delete Checkout API' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mock.callsTo('DELETE', /resources/)).toHaveLength(0);
  });

  it('disables monitoring with a PATCH containing only is_enabled', async () => {
    const mock = installMockApi()
      .get('/api/v1/resources', page())
      .on('PATCH', '/api/v1/resources/res-1', ({ body }) => ({
        body: makeResource({ is_enabled: (body as { is_enabled: boolean }).is_enabled }),
      }));
    renderWithProviders(<ResourcesPage />);
    await screen.findByRole('link', { name: 'Checkout API' });

    await userEvent.click(
      screen.getByRole('button', { name: 'Disable monitoring of Checkout API' }),
    );
    await waitFor(() => expect(mock.callsTo('PATCH', '/api/v1/resources/res-1')).toHaveLength(1));
    expect(mock.callsTo('PATCH', '/api/v1/resources/res-1')[0]?.body).toEqual({
      is_enabled: false,
    });
    expect(await screen.findByText(/Monitoring of “Checkout API” disabled/)).toBeInTheDocument();
  });

  it('runs a manual check and reports the result', async () => {
    installMockApi()
      .get('/api/v1/resources', page())
      .on('POST', '/api/v1/resources/res-1/check', {
        body: {
          id: 'res-1',
          name: 'Checkout API',
          is_enabled: true,
          current_status: 'HEALTHY',
          last_checked_at: '2026-10-28T12:00:00Z',
          next_check_at: null,
          last_response_time_ms: 12.3,
          last_http_status_code: 200,
          last_error: null,
        },
      });
    renderWithProviders(<ResourcesPage />);
    await screen.findByRole('link', { name: 'Checkout API' });

    await userEvent.click(screen.getByRole('button', { name: 'Check Checkout API now' }));
    expect(await screen.findByText(/Checkout API is healthy \(12\.3 ms\)/)).toBeInTheDocument();
  });

  it('surfaces a 409 from a manual check as a message, not a crash', async () => {
    installMockApi()
      .get('/api/v1/resources', page())
      .on('POST', '/api/v1/resources/res-1/check', {
        status: 409,
        body: { detail: 'A check is already running for this resource' },
      });
    renderWithProviders(<ResourcesPage />);
    await screen.findByRole('link', { name: 'Checkout API' });

    await userEvent.click(screen.getByRole('button', { name: 'Check Checkout API now' }));
    expect(
      await screen.findByText('A check is already running for this resource'),
    ).toBeInTheDocument();
  });

  it('does not allow manual checks on a disabled resource', async () => {
    installMockApi().get('/api/v1/resources', page([makeResource({ is_enabled: false })]));
    renderWithProviders(<ResourcesPage />);
    await screen.findByRole('link', { name: 'Checkout API' });
    expect(screen.getByRole('button', { name: 'Check Checkout API now' })).toBeDisabled();
    expect(screen.getByText('Disabled')).toBeInTheDocument();
  });
});

describe('ResourcesPage responsive table', () => {
  it('labels every cell with its column header so the card layout stays understandable', async () => {
    installMockApi().get('/api/v1/resources', page());
    renderWithProviders(<ResourcesPage />);
    const table = await screen.findByRole('table', { name: 'Monitored resources' });
    expectCellsLabelledByHeaders(table, true);
  });
});

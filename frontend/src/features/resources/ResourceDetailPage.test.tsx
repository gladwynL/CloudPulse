import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { CheckResult } from '../../lib/api/types.ts';
import {
  makeIncident,
  makeOverview,
  makeResource,
  makeRule,
  makeSummary,
  makeTimeseries,
} from '../../test/fixtures.ts';
import { installMockApi, type MockApi } from '../../test/mockApi.ts';
import { renderWithProviders } from '../../test/render.tsx';
import { ResourceDetailPage } from './ResourceDetailPage.tsx';

const check = (id: string, overrides: Partial<CheckResult> = {}): CheckResult => ({
  id,
  checked_at: '2026-10-28T11:59:00Z',
  status: 'HEALTHY',
  http_status_code: 200,
  response_time_ms: 21,
  duration_ms: 22,
  error_category: null,
  error_summary: null,
  source: 'SCHEDULED',
  created_at: '2026-10-28T11:59:00Z',
  ...overrides,
});

function api(resource = makeResource()): MockApi {
  return installMockApi()
    .get('/api/v1/resources/res-1', resource)
    .get('/api/v1/resources/res-1/metrics/summary', makeSummary())
    .get('/api/v1/resources/res-1/metrics/timeseries', makeTimeseries())
    .get('/api/v1/resources/res-1/checks', {
      resource_id: 'res-1',
      window: { start: '', end: '' },
      items: [check('c1')],
      limit: 50,
      next_cursor: null,
    })
    .get('/api/v1/resources/res-1/alert-rules', {
      items: [makeRule()],
      total: 1,
      limit: 100,
      offset: 0,
    })
    .get('/api/v1/overview', makeOverview())
    .get('/api/v1/incidents', {
      items: [makeIncident({ title: 'Earlier outage', status: 'RESOLVED' })],
      limit: 25,
      next_cursor: null,
    });
}

const renderDetail = (route = '/resources/res-1') =>
  renderWithProviders(<ResourceDetailPage />, { route, path: '/resources/:id' });

describe('ResourceDetailPage', () => {
  it('shows the header, current state, summary, rules and incidents', async () => {
    api(makeResource({ url: 'https://checkout.example.com/health?token=SECRET' }));
    renderDetail();

    expect(
      await screen.findByRole('heading', { name: 'Checkout API', level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByText('checkout.example.com/health?…')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('SECRET');

    expect(await screen.findByText('90 of 100 checks healthy')).toBeInTheDocument();
    expect(screen.getAllByText(/not continuous uptime/i).length).toBeGreaterThan(0);
    expect(await screen.findByText('Three failures')).toBeInTheDocument();
    expect(screen.getByText('1 of 20 rules used.', { exact: false })).toBeInTheDocument();
    expect(await screen.findByText('Earlier outage')).toBeInTheDocument();
  });

  it('asks the API for exactly the chosen period, never beyond 30 days', async () => {
    const mock = api();
    renderDetail('/resources/res-1?period=30d');
    await screen.findByText('90 of 100 checks healthy');

    const { query } = mock.callsTo('GET', '/api/v1/resources/res-1/metrics/summary')[0]!;
    const span = Date.parse(query.get('end')!) - Date.parse(query.get('start')!);
    expect(span).toBe(30 * 24 * 3_600_000);
  });

  it('switches period from the selector and refetches', async () => {
    const mock = api();
    renderDetail();
    await screen.findByText('90 of 100 checks healthy');

    await userEvent.click(screen.getByRole('button', { name: '1h' }));
    await waitFor(() => {
      const last = mock.callsTo('GET', '/api/v1/resources/res-1/metrics/summary').at(-1)!;
      expect(Date.parse(last.query.get('end')!) - Date.parse(last.query.get('start')!)).toBe(
        3_600_000,
      );
    });
    expect(await screen.findByText(/Summary · last hour/i)).toBeInTheDocument();
  });

  it('falls back to 24 hours for an invalid period in the URL', async () => {
    const mock = api();
    renderDetail('/resources/res-1?period=forever');
    await screen.findByText('90 of 100 checks healthy');
    const { query } = mock.callsTo('GET', '/api/v1/resources/res-1/metrics/summary')[0]!;
    expect(Date.parse(query.get('end')!) - Date.parse(query.get('start')!)).toBe(24 * 3_600_000);
  });

  it('shows chart empty states when there are no checks yet', async () => {
    api();
    renderDetail();
    expect(await screen.findAllByText('No checks in this period.')).not.toHaveLength(0);
    expect(
      screen.getByText(/Gaps mean no observations in that bucket, not downtime/),
    ).toBeInTheDocument();
  });

  it('loads older history with the cursor and reuses the window the server reported', async () => {
    const window = { start: '2026-10-27T12:00:00Z', end: '2026-10-28T12:00:00Z' };
    const mock = api().on('GET', '/api/v1/resources/res-1/checks', ({ query }) => ({
      body: query.get('cursor')
        ? {
            resource_id: 'res-1',
            window,
            items: [check('c2', { http_status_code: 503, status: 'DOWN', response_time_ms: null })],
            limit: 25,
            next_cursor: null,
          }
        : {
            resource_id: 'res-1',
            window,
            items: [check('c1')],
            limit: 25,
            next_cursor: 'cur-1',
          },
    }));
    renderDetail();
    await screen.findByText(/1 checks loaded/);

    await userEvent.click(screen.getByRole('button', { name: /load older/i }));
    expect(await screen.findByText(/2 checks loaded \(end of list\)/)).toBeInTheDocument();

    const next = mock.callsTo('GET', '/api/v1/resources/res-1/checks').at(-1);
    expect(next?.query.get('cursor')).toBe('cur-1');
    expect(next?.query.get('start')).toBe(window.start);
    expect(next?.query.get('end')).toBe(window.end);
  });

  it('filters history by result', async () => {
    const mock = api();
    renderDetail();
    await screen.findByText(/1 checks loaded/);
    await userEvent.selectOptions(screen.getByLabelText('Result'), 'DOWN');
    await waitFor(() =>
      expect(
        mock.callsTo('GET', '/api/v1/resources/res-1/checks').at(-1)?.query.get('status'),
      ).toBe('DOWN'),
    );
  });

  it('shows the error category and summary for a failed check', async () => {
    api().get('/api/v1/resources/res-1/checks', {
      resource_id: 'res-1',
      window: { start: '', end: '' },
      items: [
        check('c9', {
          status: 'DOWN',
          http_status_code: null,
          response_time_ms: null,
          error_category: 'dns',
          error_summary: 'could not resolve host',
        }),
      ],
      limit: 50,
      next_cursor: null,
    });
    renderDetail();
    expect(await screen.findByText('DNS failure')).toBeInTheDocument();
    expect(screen.getByText('could not resolve host')).toBeInTheDocument();
  });

  it('shows a warning for a disabled resource and disables the manual check', async () => {
    api(makeResource({ is_enabled: false }));
    renderDetail();
    expect(await screen.findByText('Monitoring is disabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /check now/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^enable$/i })).toBeInTheDocument();
  });

  it('deletes after confirmation, explaining the consequences', async () => {
    const mock = api().on('DELETE', '/api/v1/resources/res-1', { status: 204 });
    renderDetail();
    await screen.findByRole('heading', { name: 'Checkout API', level: 1 });

    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/all of its monitoring history/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/incidents it caused are kept/i)).toBeInTheDocument();
    expect(mock.callsTo('DELETE', /resources/)).toHaveLength(0);

    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete resource' }));
    await waitFor(() => expect(mock.callsTo('DELETE', '/api/v1/resources/res-1')).toHaveLength(1));
  });

  it('edits the resource with a PATCH containing only the changes', async () => {
    const mock = api().on('PATCH', '/api/v1/resources/res-1', ({ body }) => ({
      body: makeResource({ ...(body as object) }),
    }));
    renderDetail();
    await screen.findByRole('heading', { name: 'Checkout API', level: 1 });

    await userEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    const dialog = await screen.findByRole('dialog');
    const name = within(dialog).getByLabelText(/^Name/);
    await userEvent.clear(name);
    await userEvent.type(name, 'Checkout v2');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(mock.callsTo('PATCH', '/api/v1/resources/res-1')).toHaveLength(1));
    expect(mock.callsTo('PATCH', '/api/v1/resources/res-1')[0]?.body).toEqual({
      name: 'Checkout v2',
    });
  });

  it('warns before an edit that resets the last observation', async () => {
    api();
    renderDetail();
    await screen.findByRole('heading', { name: 'Checkout API', level: 1 });
    await userEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    const dialog = await screen.findByRole('dialog');

    const url = within(dialog).getByLabelText(/^URL to check/);
    await userEvent.clear(url);
    await userEvent.type(url, 'https://other.example.com/');
    expect(within(dialog).getByText(/resets the last observation/i)).toBeInTheDocument();
  });

  it('shows a not-found page for an unknown resource (404)', async () => {
    installMockApi().get('/api/v1/resources/res-1', { detail: 'Resource not found' }, 404);
    renderDetail();
    expect(await screen.findByText('Resource not found')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to resources' })).toBeInTheDocument();
  });

  it('shows a retryable error when the backend is down (503) and does not crash', async () => {
    installMockApi().get('/api/v1/resources/res-1', { detail: 'down' }, 503);
    renderDetail();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Service unavailable');
    expect(within(alert).getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('keeps the rest of the page usable when only the charts fail', async () => {
    api().get('/api/v1/resources/res-1/metrics/timeseries', { detail: 'boom' }, 500);
    renderDetail();
    expect(
      await screen.findByRole('heading', { name: 'Checkout API', level: 1 }),
    ).toBeInTheDocument();
    expect((await screen.findAllByText('Server error')).length).toBeGreaterThan(0);
    expect(await screen.findByText('90 of 100 checks healthy')).toBeInTheDocument();
  });
});

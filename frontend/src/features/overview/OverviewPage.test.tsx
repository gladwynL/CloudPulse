import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { makeIncident, makeOverview, makeResource } from '../../test/fixtures.ts';
import { installMockApi, type MockApi } from '../../test/mockApi.ts';
import { renderWithProviders } from '../../test/render.tsx';
import { OverviewPage } from './OverviewPage.tsx';

function api(overrides: Parameters<typeof makeOverview>[0] = {}): MockApi {
  return installMockApi()
    .get('/api/v1/overview', makeOverview(overrides))
    .get('/api/v1/resources', {
      items: [
        makeResource({ id: 'a', name: 'Alpha', current_status: 'HEALTHY' }),
        makeResource({
          id: 'b',
          name: 'Bravo',
          current_status: 'DOWN',
          last_response_time_ms: null,
        }),
        makeResource({ id: 'c', name: 'Charlie', current_status: 'DEGRADED', is_enabled: false }),
      ],
      total: 230,
      limit: 50,
      offset: 0,
    })
    .on('GET', '/api/v1/incidents', ({ query }) => ({
      body: {
        items:
          query.get('status') === 'RESOLVED'
            ? [makeIncident({ id: 'old', title: 'An old problem', status: 'RESOLVED' })]
            : [makeIncident({ id: 'new', title: 'A live problem' })],
        limit: 5,
        next_cursor: null,
      },
    }))
    .get('/health', { status: 'ok' })
    .get('/ready', { status: 'ready', checks: { database: 'ok' } });
}

describe('OverviewPage', () => {
  it('shows counts from the aggregate endpoint, not from the loaded list', async () => {
    api();
    renderWithProviders(<OverviewPage />);

    const summary = await screen.findByRole('region', { name: 'System summary' });
    const stats = within(summary);
    // 230 resources in total although only three are in the service list.
    expect(stats.getByRole('link', { name: /Resources.*230/ })).toHaveAttribute(
      'href',
      '/resources',
    );
    expect(stats.getByRole('link', { name: /Healthy.*200/ })).toHaveAttribute(
      'href',
      '/resources?status=HEALTHY',
    );
    expect(stats.getByRole('link', { name: /Degraded.*15/ })).toBeInTheDocument();
    expect(stats.getByRole('link', { name: /Down.*10/ })).toBeInTheDocument();
    expect(stats.getByRole('link', { name: /Unknown.*5/ })).toBeInTheDocument();
    expect(stats.getByRole('link', { name: /Active incidents.*3/ })).toBeInTheDocument();
    expect(stats.getByText('2 open · 1 acknowledged')).toBeInTheDocument();
    expect(stats.getByText('of 40 rules')).toBeInTheDocument();
    expect(stats.getByText('2 paused')).toBeInTheDocument();
  });

  it('describes the health distribution for assistive technology and in text', async () => {
    api();
    renderWithProviders(<OverviewPage />);
    const chart = await screen.findByRole('img', { name: /Health distribution of 230 resources/ });
    expect(chart).toHaveAccessibleName(/200 healthy, 15 degraded, 10 down, 5 unknown/);
    expect(screen.getByText('(86.96%)')).toBeInTheDocument();
  });

  it('lists the most severe services first and says the list is partial', async () => {
    api();
    renderWithProviders(<OverviewPage />);
    const table = await screen.findByRole('table', { name: /Monitored services/ });
    const names = within(table)
      .getAllByRole('link')
      .map((link) => link.textContent);
    expect(names).toEqual(['Bravo', 'Charlie', 'Alpha']);
    expect(within(table).getByText(/· paused/)).toBeInTheDocument();
    expect(screen.getByText(/Showing 3 of 230 services/)).toBeInTheDocument();
  });

  it('shows active and recently resolved incidents', async () => {
    api();
    renderWithProviders(<OverviewPage />);
    expect(await screen.findByText('A live problem')).toBeInTheDocument();
    expect(await screen.findByText('An old problem')).toBeInTheDocument();
  });

  it('states plainly when there are no active incidents', async () => {
    installMockApi()
      .get('/api/v1/overview', makeOverview())
      .get('/api/v1/resources', { items: [makeResource()], total: 1, limit: 50, offset: 0 })
      .get('/api/v1/incidents', { items: [], limit: 5, next_cursor: null })
      .get('/health', { status: 'ok' })
      .get('/ready', { status: 'ready', checks: { database: 'ok' } });
    renderWithProviders(<OverviewPage />);
    expect(await screen.findByText('No active incidents.')).toBeInTheDocument();
    expect(screen.getByText('No incidents have been resolved yet.')).toBeInTheDocument();
  });

  it('shows an onboarding empty state when nothing is monitored yet', async () => {
    api({
      resources: {
        total: 0,
        enabled: 0,
        disabled: 0,
        by_status: { UNKNOWN: 0, HEALTHY: 0, DEGRADED: 0, DOWN: 0 },
      },
    });
    renderWithProviders(<OverviewPage />);
    expect(await screen.findByText('No resources are being monitored yet')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /add your first resource/i })).toHaveAttribute(
      'href',
      '/resources?new=1',
    );
  });

  it('shows an error with retry when the API is unreachable, and recovers', async () => {
    let up = false;
    installMockApi()
      .on('GET', '/api/v1/overview', () => (up ? { body: makeOverview() } : 'network-error'))
      .get('/api/v1/resources', { items: [], total: 0, limit: 50, offset: 0 })
      .get('/api/v1/incidents', { items: [], limit: 5, next_cursor: null })
      .on('GET', '/health', 'network-error');
    renderWithProviders(<OverviewPage />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Cannot reach the API');

    up = true;
    await userEvent.click(within(alert).getByRole('button', { name: /try again/i }));
    expect(await screen.findByRole('region', { name: 'System summary' })).toBeInTheDocument();
  });

  it('degrades one panel at a time when only the service list fails', async () => {
    api();
    installMockApiOverride();
    renderWithProviders(<OverviewPage />);
    // The counts still render even though the list failed.
    expect(await screen.findByRole('region', { name: 'System summary' })).toBeInTheDocument();
    expect(await screen.findByText('Server error')).toBeInTheDocument();
    expect(screen.getByText('A live problem')).toBeInTheDocument();
  });

  it('refreshes every panel from the Refresh button', async () => {
    const mock = api();
    renderWithProviders(<OverviewPage />);
    await screen.findByRole('region', { name: 'System summary' });
    const before = mock.callsTo('GET', '/api/v1/overview').length;

    await userEvent.click(screen.getByRole('button', { name: /refresh/i }));
    await screen.findByRole('region', { name: 'System summary' });
    await expect.poll(() => mock.callsTo('GET', '/api/v1/overview').length).toBeGreaterThan(before);
  });
});

/** Make only the service-list request fail, keeping the rest of `api()` intact. */
function installMockApiOverride() {
  const original = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input.toString());
    if (url.pathname === '/api/v1/resources') {
      return Promise.resolve(
        new Response(JSON.stringify({ detail: 'boom' }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }
    return original(input, init);
  }) as typeof fetch;
}

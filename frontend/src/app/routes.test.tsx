import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ErrorBoundary } from '../components/layout/ErrorBoundary.tsx';
import { makeIncident, makeOverview, makeResource } from '../test/fixtures.ts';
import { installMockApi, type MockApi } from '../test/mockApi.ts';
import { renderWithProviders } from '../test/render.tsx';
import { AppRoutes } from './routes.tsx';

function api(): MockApi {
  return installMockApi()
    .get('/api/v1/overview', makeOverview())
    .get('/api/v1/resources', { items: [makeResource()], total: 1, limit: 25, offset: 0 })
    .get('/api/v1/alert-rules', { items: [], total: 0, limit: 25, offset: 0 })
    .get('/api/v1/incidents', { items: [makeIncident()], limit: 25, next_cursor: null })
    .get('/health', { status: 'ok' })
    .get('/ready', { status: 'ready', checks: { database: 'ok' } });
}

describe('routing and shell', () => {
  it.each([
    ['/', 'Overview'],
    ['/resources', 'Resources'],
    ['/alerts', 'Alert rules'],
    ['/incidents', 'Incidents'],
  ])('renders %s with its heading', async (route, heading) => {
    api();
    renderWithProviders(<AppRoutes />, { route });
    expect(await screen.findByRole('heading', { name: heading, level: 1 })).toBeInTheDocument();
  });

  it('renders the resource and incident detail routes', async () => {
    api()
      .get('/api/v1/resources/res-1', makeResource())
      .get('/api/v1/incidents/inc-1', makeIncident())
      .get('/api/v1/incidents/inc-1/events', { incident_id: 'inc-1', items: [] });
    const first = renderWithProviders(<AppRoutes />, { route: '/resources/res-1' });
    // The detail page pulls in the chart library lazily, which can take a moment on first load.
    expect(
      await screen.findByRole('heading', { name: 'Checkout API', level: 1 }, { timeout: 10_000 }),
    ).toBeInTheDocument();
    first.unmount();

    renderWithProviders(<AppRoutes />, { route: '/incidents/inc-1' });
    expect(
      await screen.findByRole('heading', {
        name: 'WARNING: Three failures on Checkout API',
        level: 1,
      }),
    ).toBeInTheDocument();
  });

  it('shows a friendly page for an unknown URL', async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: '/nope/never' });
    expect(await screen.findByText('Page not found')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to overview/i })).toHaveAttribute('href', '/');
  });

  it('offers primary navigation, marks the current page and links to every section', async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: '/alerts' });
    const nav = await screen.findByRole('navigation', { name: 'Primary' });
    expect(within(nav).getByRole('link', { name: 'Alert rules' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    for (const [name, href] of [
      ['Overview', '/'],
      ['Resources', '/resources'],
      ['Incidents', '/incidents'],
    ] as const) {
      expect(within(nav).getByRole('link', { name: new RegExp(name) })).toHaveAttribute(
        'href',
        href,
      );
    }
  });

  it('shows the active incident count next to Incidents, with a text alternative', async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: '/alerts' });
    expect(await screen.findByLabelText('3 active incidents')).toHaveTextContent('3');
  });

  it('provides a skip link and a focusable main landmark', async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: '/' });
    expect(await screen.findByRole('link', { name: 'Skip to content' })).toHaveAttribute(
      'href',
      '#main',
    );
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main');
  });

  it('states the local-only, no-authentication boundary', async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: '/' });
    expect(await screen.findByText(/no authentication/i)).toBeInTheDocument();
  });

  it('toggles the navigation drawer with correct ARIA state and closes on Escape', async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: '/' });
    const toggle = await screen.findByRole('button', { name: 'Open navigation' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    await userEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'Close navigation' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );

    await userEvent.keyboard('{Escape}');
    expect(screen.getByRole('button', { name: 'Open navigation' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('closes the drawer after navigating', async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: '/' });
    await userEvent.click(await screen.findByRole('button', { name: 'Open navigation' }));
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    await userEvent.click(within(nav).getByRole('link', { name: 'Resources' }));
    expect(await screen.findByRole('button', { name: 'Open navigation' })).toBeInTheDocument();
  });
});

describe('API status indicator', () => {
  const pill = () => screen.findByRole('status', { name: /API/ });

  it('reports online when /health and /ready both succeed', async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: '/' });
    const status = await screen.findByLabelText(/^API online/);
    expect(status).toHaveAttribute('data-connectivity', 'online');
  });

  it('reports degraded when the API is up but the database is not ready', async () => {
    api().get('/ready', { status: 'unavailable', checks: { database: 'unavailable' } }, 503);
    renderWithProviders(<AppRoutes />, { route: '/' });
    expect(await screen.findByLabelText(/database unavailable/)).toHaveAttribute(
      'data-connectivity',
      'degraded',
    );
  });

  it('reports offline when the API cannot be reached, without crashing the page', async () => {
    installMockApi()
      .on('GET', '/health', 'network-error')
      .on('GET', '/api/v1/overview', 'network-error')
      .on('GET', '/api/v1/resources', 'network-error')
      .on('GET', '/api/v1/incidents', 'network-error');
    renderWithProviders(<AppRoutes />, { route: '/' });
    expect(await screen.findByLabelText(/API unreachable/)).toHaveAttribute(
      'data-connectivity',
      'offline',
    );
    // The shell and navigation are still there, and the page explains the problem.
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
    expect(await screen.findByText('Cannot reach the API')).toBeInTheDocument();
    void pill;
  });
});

describe('ErrorBoundary', () => {
  function Boom(): never {
    throw new Error('render exploded');
  }

  it('replaces a crashed render with a recovery screen and logs only the message', () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderWithProviders(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong in the dashboard');
    expect(screen.getByRole('button', { name: /reload the page/i })).toBeInTheDocument();
    expect(log.mock.calls.some((call) => call[0] === 'UI render error:')).toBe(true);
    log.mockRestore();
  });

  it('renders children when nothing fails', () => {
    renderWithProviders(
      <ErrorBoundary>
        <p>fine</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText('fine')).toBeInTheDocument();
  });
});

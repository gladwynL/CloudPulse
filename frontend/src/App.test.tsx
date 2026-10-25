import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App.tsx';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function mockProbes(responses: {
  health: () => Promise<Response>;
  ready: () => Promise<Response>;
}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = String(input);
    return url.endsWith('/health') ? responses.health() : responses.ready();
  });
}

describe('App shell', () => {
  beforeEach(() => {
    mockProbes({
      health: async () => jsonResponse(200, { status: 'ok' }),
      ready: async () => jsonResponse(200, { status: 'ready', checks: { database: 'ok' } }),
    });
  });

  it('renders CloudPulse branding and navigation', () => {
    render(<App />);
    expect(screen.getByRole('link', { name: 'CloudPulse home' })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
  });

  it('renders the main heading', () => {
    render(<App />);
    expect(
      screen.getByRole('heading', { level: 1, name: 'Cloud Monitoring & Observability' }),
    ).toBeInTheDocument();
  });

  it('renders the system status area and roadmap', () => {
    render(<App />);
    expect(screen.getByRole('heading', { name: 'System status' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Roadmap' })).toBeInTheDocument();
    expect(screen.getByText('Current')).toBeInTheDocument();
  });
});

describe('System status panel', () => {
  it('shows operational when both probes succeed', async () => {
    mockProbes({
      health: async () => jsonResponse(200, { status: 'ok' }),
      ready: async () => jsonResponse(200, { status: 'ready', checks: { database: 'ok' } }),
    });
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('status-api')).toHaveTextContent('Operational'));
    expect(screen.getByTestId('status-database')).toHaveTextContent('Operational');
  });

  it('shows database unavailable when readiness returns 503', async () => {
    mockProbes({
      health: async () => jsonResponse(200, { status: 'ok' }),
      ready: async () =>
        jsonResponse(503, { status: 'unavailable', checks: { database: 'unavailable' } }),
    });
    render(<App />);
    await waitFor(() =>
      expect(screen.getByTestId('status-database')).toHaveTextContent('Unavailable'),
    );
    expect(screen.getByTestId('status-api')).toHaveTextContent('Operational');
  });

  it('shows unreachable when the API cannot be contacted', async () => {
    mockProbes({
      health: () => Promise.reject(new TypeError('Failed to fetch')),
      ready: () => Promise.reject(new TypeError('Failed to fetch')),
    });
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('status-api')).toHaveTextContent('Unreachable'));
    expect(screen.getByTestId('status-database')).toHaveTextContent('Unreachable');
  });

  it('re-runs the probes when Refresh is clicked', async () => {
    const fetchSpy = mockProbes({
      health: async () => jsonResponse(200, { status: 'ok' }),
      ready: async () => jsonResponse(200, { status: 'ready', checks: { database: 'ok' } }),
    });
    render(<App />);
    const button = await screen.findByRole('button', { name: 'Refresh' });
    const callsBefore = fetchSpy.mock.calls.length;
    await userEvent.click(button);
    await waitFor(() => expect(fetchSpy.mock.calls.length).toBe(callsBefore + 2));
  });
});

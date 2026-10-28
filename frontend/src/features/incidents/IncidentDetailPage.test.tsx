import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Incident } from '../../lib/api/types.ts';
import { makeEvent, makeIncident } from '../../test/fixtures.ts';
import { installMockApi } from '../../test/mockApi.ts';
import { renderWithProviders } from '../../test/render.tsx';
import { IncidentDetailPage } from './IncidentDetailPage.tsx';
import { Timeline } from './Timeline.tsx';

const renderDetail = () =>
  renderWithProviders(<IncidentDetailPage />, {
    route: '/incidents/inc-1',
    path: '/incidents/:id',
  });

const timeline = (items = [makeEvent()]) => ({ incident_id: 'inc-1', items });

const check = {
  id: 'chk-1',
  checked_at: '2026-10-28T11:50:00Z',
  status: 'DEGRADED',
  http_status_code: 404,
  response_time_ms: 3.6,
  duration_ms: 4,
  error_category: 'unexpected_status',
  error_summary: 'expected status 200, got 404',
  source: 'SCHEDULED',
  created_at: '2026-10-28T11:50:00Z',
};

describe('IncidentDetailPage', () => {
  it('shows the incident, its triggering measurement and its timeline', async () => {
    installMockApi()
      .get('/api/v1/incidents/inc-1', makeIncident({ opened_by_check_id: 'chk-1' }))
      .get(
        '/api/v1/incidents/inc-1/events',
        timeline([makeEvent({ details: { observed_status: 'DEGRADED', failure_streak: 3 } })]),
      )
      .get('/api/v1/resources/res-1/checks', {
        resource_id: 'res-1',
        window: { start: '', end: '' },
        items: [check],
        limit: 10,
        next_cursor: null,
      });
    renderDetail();

    expect(
      await screen.findByRole('heading', { name: 'WARNING: Three failures on Checkout API' }),
    ).toBeInTheDocument();
    expect(screen.getAllByText('Open').length).toBeGreaterThan(0);
    expect(await screen.findByText('expected status 200, got 404')).toBeInTheDocument();
    expect(screen.getByText('404')).toBeInTheDocument();

    const list = screen.getByRole('list', { name: 'Incident timeline' });
    expect(within(list).getByText('Incident opened')).toBeInTheDocument();
    expect(within(list).getByText(/Observed status: DEGRADED/)).toBeInTheDocument();
    expect(within(list).getByText(/Consecutive matches: 3/)).toBeInTheDocument();
  });

  it('acknowledges an open incident and then shows it as acknowledged', async () => {
    let current: Incident = makeIncident();
    const mock = installMockApi()
      .on('GET', '/api/v1/incidents/inc-1', () => ({ body: current }))
      .get('/api/v1/incidents/inc-1/events', timeline())
      .on('POST', '/api/v1/incidents/inc-1/acknowledge', () => {
        current = makeIncident({
          status: 'ACKNOWLEDGED',
          acknowledged_at: '2026-10-28T12:01:00Z',
        });
        return { body: current };
      });
    renderDetail();

    await userEvent.click(await screen.findByRole('button', { name: /acknowledge/i }));

    expect(await screen.findByText('Incident acknowledged.')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /acknowledge/i })).not.toBeInTheDocument(),
    );
    expect(screen.getAllByText('Acknowledged').length).toBeGreaterThan(0);
    expect(mock.callsTo('POST', '/api/v1/incidents/inc-1/acknowledge')).toHaveLength(1);
  });

  it('cannot be submitted twice while the request is in flight', async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const mock = installMockApi()
      .get('/api/v1/incidents/inc-1', makeIncident())
      .get('/api/v1/incidents/inc-1/events', timeline())
      .on('POST', '/api/v1/incidents/inc-1/acknowledge', async () => {
        await gate;
        return { body: makeIncident({ status: 'ACKNOWLEDGED' }) };
      });
    renderDetail();

    const button = await screen.findByRole('button', { name: /acknowledge/i });
    await userEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    await userEvent.click(button);
    release();

    await screen.findByText('Incident acknowledged.');
    expect(mock.callsTo('POST', '/api/v1/incidents/inc-1/acknowledge')).toHaveLength(1);
  });

  it('handles a 409 because the incident resolved meanwhile, and shows the real state', async () => {
    let current: Incident = makeIncident();
    installMockApi()
      .on('GET', '/api/v1/incidents/inc-1', () => ({ body: current }))
      .get('/api/v1/incidents/inc-1/events', timeline())
      .on('POST', '/api/v1/incidents/inc-1/acknowledge', () => {
        current = makeIncident({
          status: 'RESOLVED',
          resolved_at: '2026-10-28T12:02:00Z',
          resolution_reason: 'RECOVERED',
        });
        return { status: 409, body: { detail: 'Incident is already resolved' } };
      });
    renderDetail();

    await userEvent.click(await screen.findByRole('button', { name: /acknowledge/i }));

    expect(await screen.findByText('Incident is already resolved')).toBeInTheDocument();
    // The refetch replaces the stale view: resolved, no acknowledge button, reason shown.
    expect(await screen.findByText('Recovered automatically')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /acknowledge/i })).not.toBeInTheDocument();
  });

  it('offers no way to resolve an incident manually', async () => {
    installMockApi()
      .get('/api/v1/incidents/inc-1', makeIncident())
      .get('/api/v1/incidents/inc-1/events', timeline());
    renderDetail();
    await screen.findByRole('button', { name: /acknowledge/i });
    expect(screen.queryByRole('button', { name: /resolve/i })).not.toBeInTheDocument();
  });

  it('says plainly that no user identity is attached to an acknowledgement', async () => {
    installMockApi()
      .get('/api/v1/incidents/inc-1', makeIncident())
      .get('/api/v1/incidents/inc-1/events', timeline());
    renderDetail();
    expect(await screen.findByText(/No user identity is attached/)).toBeInTheDocument();
  });

  it('shows a resolved incident without actions, with its duration and reason', async () => {
    installMockApi()
      .get(
        '/api/v1/incidents/inc-1',
        makeIncident({
          status: 'RESOLVED',
          resolved_at: '2026-10-28T11:54:12Z',
          resolution_reason: 'RULE_DISABLED',
        }),
      )
      .get('/api/v1/incidents/inc-1/events', timeline());
    renderDetail();

    expect(await screen.findByText('Rule was disabled')).toBeInTheDocument();
    expect(screen.getByText('after 4m 12s')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /acknowledge/i })).not.toBeInTheDocument();
  });

  it('keeps the record readable after the resource and rule were deleted', async () => {
    installMockApi()
      .get(
        '/api/v1/incidents/inc-1',
        makeIncident({
          resource_id: null,
          alert_rule_id: null,
          status: 'RESOLVED',
          resolved_at: '2026-10-28T11:54:12Z',
          resolution_reason: 'RESOURCE_DELETED',
        }),
      )
      .get('/api/v1/incidents/inc-1/events', timeline());
    renderDetail();

    expect(await screen.findByText('Checkout API')).toBeInTheDocument();
    expect(screen.getAllByText('(deleted)')).toHaveLength(2);
    expect(screen.queryByRole('link', { name: 'Checkout API' })).not.toBeInTheDocument();
    expect(screen.getByText(/resource was deleted, so its history is gone/i)).toBeInTheDocument();
  });

  it('explains when the triggering measurement is past retention', async () => {
    installMockApi()
      .get('/api/v1/incidents/inc-1', makeIncident({ opened_by_check_id: 'chk-1' }))
      .get('/api/v1/incidents/inc-1/events', timeline())
      .get('/api/v1/resources/res-1/checks', {
        resource_id: 'res-1',
        window: { start: '', end: '' },
        items: [],
        limit: 10,
        next_cursor: null,
      });
    renderDetail();
    expect(await screen.findByText(/past the history retention period/i)).toBeInTheDocument();
  });

  it('shows a not-found page for an unknown incident (404)', async () => {
    installMockApi().get('/api/v1/incidents/inc-1', { detail: 'Incident not found' }, 404);
    renderDetail();
    expect(await screen.findByText('Incident not found')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to incidents' })).toBeInTheDocument();
  });

  it('shows a retryable error when the API is down (503)', async () => {
    installMockApi().get('/api/v1/incidents/inc-1', { detail: 'down' }, 503);
    renderDetail();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Service unavailable');
    expect(within(alert).getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });
});

describe('Timeline', () => {
  it('labels every event type and attributes manual actions without claiming an identity', () => {
    renderWithProviders(
      <Timeline
        events={[
          makeEvent({ id: 'a', event_type: 'OPENED' }),
          makeEvent({ id: 'b', event_type: 'ACKNOWLEDGED', actor: 'MANUAL' }),
          makeEvent({ id: 'c', event_type: 'RECOVERED' }),
          makeEvent({ id: 'd', event_type: 'RULE_RECONFIGURED' }),
        ]}
      />,
    );
    expect(screen.getByText('Incident opened')).toBeInTheDocument();
    expect(screen.getByText('Acknowledged')).toBeInTheDocument();
    expect(screen.getByText('Recovered: resolved automatically')).toBeInTheDocument();
    expect(screen.getByText('Resolved: the rule was reconfigured')).toBeInTheDocument();
    expect(screen.getByText(/Manual \(API call, no verified identity\)/)).toBeInTheDocument();
  });

  it('shows only known detail fields', () => {
    renderWithProviders(
      <Timeline
        events={[makeEvent({ details: { observed_status: 'DOWN', internal_secret: 'hidden' } })]}
      />,
    );
    expect(screen.getByText(/Observed status: DOWN/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('hidden');
  });
});

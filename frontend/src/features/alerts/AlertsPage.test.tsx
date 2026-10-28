import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { makeOverview, makeResource, makeRule, makeRuleListItem } from '../../test/fixtures.ts';
import { installMockApi, type MockApi } from '../../test/mockApi.ts';
import { renderWithProviders } from '../../test/render.tsx';
import { AlertsPage } from './AlertsPage.tsx';

const page = (items = [makeRuleListItem()], total = items.length) => ({
  items,
  total,
  limit: 25,
  offset: 0,
});

/** Everything the page and its create/edit form read. */
function baseApi(items = [makeRuleListItem()], existingRules = 0): MockApi {
  return installMockApi()
    .get('/api/v1/alert-rules', page(items))
    .get('/api/v1/overview', makeOverview())
    .get('/api/v1/resources', {
      items: [makeResource(), makeResource({ id: 'res-2', name: 'Search' })],
      total: 2,
      limit: 200,
      offset: 0,
    })
    .get(/\/api\/v1\/resources\/[^/]+\/alert-rules$/, {
      items: Array.from({ length: existingRules }, (_, i) => makeRule({ id: `r${i}` })),
      total: existingRules,
      limit: 100,
      offset: 0,
    });
}

describe('AlertsPage list', () => {
  it('describes each rule in plain language with its state', async () => {
    baseApi([
      makeRuleListItem({ current_state: 'FIRING', failure_streak: 4, recovery_streak: 1 }),
      makeRuleListItem({
        id: 'rule-2',
        name: 'Slow',
        rule_type: 'LATENCY_THRESHOLD',
        operator: 'GREATER_THAN',
        threshold: 800,
        consecutive_failures: 2,
        severity: 'CRITICAL',
      }),
    ]);
    renderWithProviders(<AlertsPage />);

    expect(await screen.findByText('Three failures')).toBeInTheDocument();
    expect(screen.getByText('3 consecutive unhealthy checks')).toBeInTheDocument();
    expect(screen.getByText('Response time > 800 ms for 2 consecutive checks')).toBeInTheDocument();
    const table = within(screen.getByRole('table', { name: 'Alert rules' }));
    expect(table.getByText('Firing')).toBeInTheDocument();
    expect(table.getByText(/streak 4 · recovery 1\/1/)).toBeInTheDocument();
    expect(table.getByText('Critical')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Checkout API' })[0]).toHaveAttribute(
      'href',
      '/resources/res-1',
    );
  });

  it('shows a helpful empty state', async () => {
    baseApi([]);
    renderWithProviders(<AlertsPage />);
    expect(await screen.findByText('No alert rules yet')).toBeInTheDocument();
  });

  it('shows an error with retry when the API fails (503)', async () => {
    installMockApi().get('/api/v1/alert-rules', { detail: 'down' }, 503);
    renderWithProviders(<AlertsPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Service unavailable');
  });

  it('sends filters to the API', async () => {
    const mock = baseApi();
    renderWithProviders(<AlertsPage />);
    await screen.findByText('Three failures');

    await userEvent.selectOptions(screen.getByLabelText('State'), 'FIRING');
    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'CRITICAL');
    await waitFor(() => {
      const last = mock.callsTo('GET', '/api/v1/alert-rules').at(-1);
      expect(last?.query.get('current_state')).toBe('FIRING');
      expect(last?.query.get('severity')).toBe('CRITICAL');
    });
  });

  it('can be scoped to one resource from the URL', async () => {
    const mock = baseApi();
    renderWithProviders(<AlertsPage />, { route: '/alerts?resource=res-1' });
    await screen.findByText('Three failures');
    expect(mock.callsTo('GET', '/api/v1/alert-rules')[0]?.query.get('resource_id')).toBe('res-1');
    expect(screen.getByRole('button', { name: /showing one resource/i })).toBeInTheDocument();
  });
});

describe('AlertsPage create', () => {
  async function openForm() {
    await screen.findByText('Three failures');
    await userEvent.click(screen.getByRole('button', { name: /create rule/i }));
    return await screen.findByRole('dialog');
  }

  it('requires a resource and a name before sending', async () => {
    const mock = baseApi();
    renderWithProviders(<AlertsPage />);
    const dialog = await openForm();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Create rule' }));
    expect(
      within(dialog).getByText('Choose the resource this rule applies to.'),
    ).toBeInTheDocument();

    await userEvent.selectOptions(await within(dialog).findByLabelText(/^Resource/), 'res-1');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create rule' }));
    expect(within(dialog).getByText('Enter a name.')).toBeInTheDocument();
    expect(mock.callsTo('POST', /alert-rules/)).toHaveLength(0);
  });

  it('shows only the inputs relevant to the chosen rule type', async () => {
    baseApi();
    renderWithProviders(<AlertsPage />);
    const dialog = await openForm();

    expect(within(dialog).getByLabelText(/Consecutive failed checks/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/Threshold/)).not.toBeInTheDocument();

    await userEvent.selectOptions(within(dialog).getByLabelText('Rule type'), 'LATENCY_THRESHOLD');
    expect(within(dialog).getByLabelText(/Threshold/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Consecutive slow checks/)).toBeInTheDocument();

    await userEvent.selectOptions(within(dialog).getByLabelText('Rule type'), 'STATUS');
    expect(within(dialog).getByLabelText('Fire when status is')).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/Threshold/)).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/Consecutive/)).not.toBeInTheDocument();
  });

  it('creates a latency rule with exactly its own fields', async () => {
    const mock = baseApi().on('POST', '/api/v1/resources/res-1/alert-rules', ({ body }) => ({
      status: 201,
      body: makeRule({ name: (body as { name: string }).name }),
    }));
    renderWithProviders(<AlertsPage />);
    const dialog = await openForm();

    await userEvent.selectOptions(await within(dialog).findByLabelText(/^Resource/), 'res-1');
    await userEvent.type(within(dialog).getByLabelText(/^Rule name/), 'Slow API');
    await userEvent.selectOptions(within(dialog).getByLabelText('Rule type'), 'LATENCY_THRESHOLD');
    await userEvent.clear(within(dialog).getByLabelText(/Threshold/));
    await userEvent.type(within(dialog).getByLabelText(/Threshold/), '750');
    await userEvent.selectOptions(within(dialog).getByLabelText('Severity'), 'CRITICAL');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create rule' }));

    await waitFor(() =>
      expect(mock.callsTo('POST', '/api/v1/resources/res-1/alert-rules')).toHaveLength(1),
    );
    const body = mock.callsTo('POST', '/api/v1/resources/res-1/alert-rules')[0]?.body;
    expect(body).toMatchObject({
      name: 'Slow API',
      rule_type: 'LATENCY_THRESHOLD',
      operator: 'GREATER_THAN',
      threshold: 750,
      consecutive_failures: 3,
      severity: 'CRITICAL',
    });
    expect(body).not.toHaveProperty('status_condition');
    expect(await screen.findByText(/Created rule “Slow API”/)).toBeInTheDocument();
  });

  it('blocks creation when the resource is at the rule limit', async () => {
    baseApi([makeRuleListItem()], 20);
    renderWithProviders(<AlertsPage />);
    const dialog = await openForm();

    await userEvent.selectOptions(await within(dialog).findByLabelText(/^Resource/), 'res-1');
    expect(await within(dialog).findByText('Rule limit reached')).toBeInTheDocument();
    expect(within(dialog).getByText(/already has 20 of 20 rules/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Create rule' })).toBeDisabled();
  });

  it('shows how many rules a resource has against the limit', async () => {
    baseApi([makeRuleListItem()], 3);
    renderWithProviders(<AlertsPage />);
    const dialog = await openForm();
    await userEvent.selectOptions(await within(dialog).findByLabelText(/^Resource/), 'res-1');
    expect(await within(dialog).findByText(/has 3 of 20 allowed rules/)).toBeInTheDocument();
  });

  it('shows a server validation error on the right field (422)', async () => {
    baseApi().on('POST', '/api/v1/resources/res-1/alert-rules', {
      status: 422,
      body: { detail: [{ loc: ['body', 'name'], msg: 'A rule with this name already exists' }] },
    });
    renderWithProviders(<AlertsPage />);
    const dialog = await openForm();

    await userEvent.selectOptions(await within(dialog).findByLabelText(/^Resource/), 'res-1');
    await userEvent.type(within(dialog).getByLabelText(/^Rule name/), 'Dup');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create rule' }));

    expect(
      await within(dialog).findByText('A rule with this name already exists'),
    ).toBeInTheDocument();
  });

  it('reports a 409 conflict (such as the rule limit) inside the dialog', async () => {
    baseApi().on('POST', '/api/v1/resources/res-1/alert-rules', {
      status: 409,
      body: { detail: 'Resource already has the maximum number of alert rules' },
    });
    renderWithProviders(<AlertsPage />);
    const dialog = await openForm();

    await userEvent.selectOptions(await within(dialog).findByLabelText(/^Resource/), 'res-1');
    await userEvent.type(within(dialog).getByLabelText(/^Rule name/), 'Another');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create rule' }));

    expect(
      await within(dialog).findByText('Resource already has the maximum number of alert rules'),
    ).toBeInTheDocument();
  });
});

describe('AlertsPage edit, disable and delete', () => {
  const firing = makeRuleListItem({ current_state: 'FIRING', failure_streak: 5 });

  it('warns that reconfiguring a firing rule resolves its incident, and shows it after saving', async () => {
    const mock = baseApi([firing]).on('PATCH', '/api/v1/alert-rules/rule-1', ({ body }) => ({
      body: makeRule({ ...(body as object), current_state: 'OK' }),
    }));
    renderWithProviders(<AlertsPage />);
    await screen.findByText('Three failures');

    await userEvent.click(screen.getByRole('button', { name: 'Edit rule Three failures' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('This rule is firing right now')).toBeInTheDocument();
    expect(
      within(dialog).queryByText('This will resolve the active incident'),
    ).not.toBeInTheDocument();

    const field = within(dialog).getByLabelText(/Consecutive failed checks/);
    await userEvent.clear(field);
    await userEvent.type(field, '5');
    expect(within(dialog).getByText('This will resolve the active incident')).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(mock.callsTo('PATCH', '/api/v1/alert-rules/rule-1')).toHaveLength(1),
    );
    expect(mock.callsTo('PATCH', '/api/v1/alert-rules/rule-1')[0]?.body).toEqual({
      consecutive_failures: 5,
    });
    expect(await screen.findByText(/Its active incident was resolved/)).toBeInTheDocument();
  });

  it('does not warn when only the name changes', async () => {
    baseApi([firing]);
    renderWithProviders(<AlertsPage />);
    await screen.findByText('Three failures');

    await userEvent.click(screen.getByRole('button', { name: 'Edit rule Three failures' }));
    const dialog = await screen.findByRole('dialog');
    const name = within(dialog).getByLabelText(/^Rule name/);
    await userEvent.type(name, ' v2');
    expect(
      within(dialog).queryByText('This will resolve the active incident'),
    ).not.toBeInTheDocument();
  });

  it('cannot change the type of an existing rule', async () => {
    baseApi();
    renderWithProviders(<AlertsPage />);
    await screen.findByText('Three failures');
    await userEvent.click(screen.getByRole('button', { name: 'Edit rule Three failures' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByLabelText('Rule type')).not.toBeInTheDocument();
    expect(within(dialog).getByText('Consecutive failures')).toBeInTheDocument();
  });

  it('disables a rule with a minimal PATCH', async () => {
    const mock = baseApi().on('PATCH', '/api/v1/alert-rules/rule-1', {
      body: makeRule({ is_enabled: false }),
    });
    renderWithProviders(<AlertsPage />);
    await screen.findByText('Three failures');

    await userEvent.click(screen.getByRole('button', { name: 'Disable rule Three failures' }));
    await waitFor(() =>
      expect(mock.callsTo('PATCH', '/api/v1/alert-rules/rule-1')).toHaveLength(1),
    );
    expect(mock.callsTo('PATCH', '/api/v1/alert-rules/rule-1')[0]?.body).toEqual({
      is_enabled: false,
    });
  });

  it('confirms deletion and flags that a firing rule’s incident will be resolved', async () => {
    const mock = baseApi([firing]).on('DELETE', '/api/v1/alert-rules/rule-1', { status: 204 });
    renderWithProviders(<AlertsPage />);
    await screen.findByText('Three failures');

    await userEvent.click(screen.getByRole('button', { name: 'Delete rule Three failures' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/its active incident will be resolved/i)).toBeInTheDocument();
    expect(mock.callsTo('DELETE', /alert-rules/)).toHaveLength(0);

    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete rule' }));
    await waitFor(() =>
      expect(mock.callsTo('DELETE', '/api/v1/alert-rules/rule-1')).toHaveLength(1),
    );
    expect(await screen.findByText(/Deleted rule “Three failures”/)).toBeInTheDocument();
  });

  it('reports a failed delete (404, already gone) without crashing', async () => {
    baseApi().on('DELETE', '/api/v1/alert-rules/rule-1', {
      status: 404,
      body: { detail: 'Alert rule not found' },
    });
    renderWithProviders(<AlertsPage />);
    await screen.findByText('Three failures');

    await userEvent.click(screen.getByRole('button', { name: 'Delete rule Three failures' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete rule' }));
    expect(await screen.findByText('Alert rule not found')).toBeInTheDocument();
  });
});

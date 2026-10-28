import type { Page } from '@playwright/test';
import {
  CHECK_INTERVAL,
  DEMO_TARGET,
  deleteResourceIfExists,
  expect,
  test,
  uniqueName,
  API_URL,
} from './fixtures';

/**
 * The whole product loop in a real browser against the real stack:
 * add a service, watch it become healthy, add an alert rule, break it, see the incident,
 * acknowledge it, fix it, see it resolve, and delete the service.
 *
 * The service points at the demo target. Breaking it means changing *its own* URL to a path that
 * answers 404 (reported as Degraded), so nothing shared (like the demo container) is touched and
 * no other resource is affected.
 */
test.describe.serial('monitoring workflow', () => {
  const name = uniqueName('flow');
  const ruleName = `${name}-rule`;
  const incidentTitle = `WARNING: ${ruleName} on ${name}`;
  let resourceId: string | null = null;

  const header = (page: Page) => page.locator('header.page__header');

  /** Capture documentation screenshots when asked (`E2E_SCREENSHOTS=1`); a no-op otherwise. */
  async function shot(page: Page, file: string) {
    if (!process.env.E2E_SCREENSHOTS) return;
    await page.screenshot({ path: `../docs/screenshots/${file}.png`, animations: 'disabled' });
  }

  /** Re-run an assertion, reloading between attempts, until the backend's checks catch up. */
  async function eventually(page: Page, assertion: () => Promise<void>, timeout = 90_000) {
    await expect(async () => {
      await page.reload();
      await assertion();
    }).toPass({ timeout, intervals: [2_000, 3_000, 5_000] });
  }

  test.afterAll(async ({ request }) => {
    // Cleanup if the scenario stopped early. Only the resource this run created is touched.
    await deleteResourceIfExists(request, resourceId);
  });

  test('the overview loads from the live API', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible();
    await expect(page.getByLabel(/^API online/)).toBeVisible();
    await expect(page.getByText(/no authentication/i)).toBeVisible();
  });

  test('add a resource and watch it become healthy', async ({ page }) => {
    await page.goto('/resources');
    await page.getByRole('button', { name: 'Add resource' }).first().click();

    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Name', { exact: true }).fill(name);
    await dialog.getByLabel('URL to check').fill(`${DEMO_TARGET}/`);
    await dialog.getByLabel('Check interval (seconds)').fill(CHECK_INTERVAL);
    await dialog.getByLabel('Timeout (seconds)').fill(CHECK_INTERVAL);
    await dialog.getByRole('button', { name: 'Add resource' }).click();

    await expect(page).toHaveURL(/\/resources\/[0-9a-f-]{36}$/);
    resourceId = page.url().split('/').pop() ?? null;
    await expect(header(page).getByRole('heading', { name, level: 1 })).toBeVisible();

    await eventually(page, async () => {
      await expect(header(page).getByText('Healthy', { exact: true })).toBeVisible({
        timeout: 3_000,
      });
    });
    await expect(page.getByText('HTTP 200', { exact: true })).toBeVisible();
  });

  test('the detail page shows a summary, charts and history built from real checks', async ({
    page,
  }) => {
    await page.goto(`/resources/${resourceId}`);
    await eventually(page, async () => {
      await expect(page.getByRole('img', { name: /Response time over time/ })).toBeVisible({
        timeout: 3_000,
      });
    });
    await expect(
      page.getByRole('img', { name: /Availability percentage over time/ }),
    ).toBeVisible();
    await expect(page.getByText(/checks healthy/)).toBeVisible();
    await expect(page.getByText(/not continuous uptime/i).first()).toBeVisible();
    await expect(
      page.getByRole('table', { name: /Check history/ }).getByRole('row'),
    ).not.toHaveCount(1);

    // Other periods are valid requests too (the API caps windows at 30 days).
    for (const period of ['1h', '7d', '30d', '24h']) {
      await page.getByRole('button', { name: period, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`period=${period}`));
      await expect(page.getByRole('heading', { name: /^Summary/ })).toBeVisible();
    }
    await expect(page.getByRole('alert')).toHaveCount(0);
    await shot(page, 'resource-detail');
  });

  test('create an alert rule from the Alert rules page', async ({ page }) => {
    await page.goto('/alerts');
    await page.getByRole('button', { name: 'Create rule' }).first().click();

    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Resource').selectOption({ label: name });
    await dialog.getByLabel('Rule name').fill(ruleName);
    await dialog.getByLabel(/Consecutive failed checks/).fill('2');
    await dialog.getByLabel(/Healthy checks needed to recover/).fill('1');
    await dialog.getByRole('button', { name: 'Create rule' }).click();

    await expect(page.getByText(`Created rule “${ruleName}”.`)).toBeVisible();
    const row = page.getByRole('row', { name: new RegExp(ruleName) });
    await expect(row).toBeVisible();
    await expect(row.getByText('2 consecutive unhealthy checks')).toBeVisible();
  });

  test('break the service: an incident opens', async ({ page }) => {
    await page.goto(`/resources/${resourceId}`);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('URL to check').fill(`${DEMO_TARGET}/missing`);
    await expect(dialog.getByText(/resets the last observation/i)).toBeVisible();
    await dialog.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByText(`Saved “${name}”.`)).toBeVisible();

    await eventually(page, async () => {
      await expect(header(page).getByText('Degraded', { exact: true })).toBeVisible({
        timeout: 3_000,
      });
    });

    await page.goto('/incidents');
    await eventually(page, async () => {
      await expect(page.getByRole('link', { name: incidentTitle })).toBeVisible({ timeout: 3_000 });
    });
    const row = page.getByRole('row', { name: new RegExp(incidentTitle) });
    await expect(row.getByText('Open', { exact: true })).toBeVisible();
    await expect(row.getByText('Warning', { exact: true })).toBeVisible();
    await shot(page, 'incidents');
  });

  test('the incident shows what triggered it, then can be acknowledged once', async ({ page }) => {
    await page.goto('/incidents');
    await page.getByRole('link', { name: incidentTitle }).click();

    await expect(page.getByRole('heading', { name: incidentTitle, level: 1 })).toBeVisible();
    await expect(page.getByRole('link', { name, exact: true })).toHaveAttribute(
      'href',
      `/resources/${resourceId}`,
    );
    await expect(page.getByRole('heading', { name: 'Triggering check' })).toBeVisible();
    await expect(page.getByText('expected status 200, got 404')).toBeVisible();
    await expect(page.getByRole('list', { name: 'Incident timeline' })).toContainText(
      'Incident opened',
    );
    await expect(page.getByText(/No user identity is attached/)).toBeVisible();
    await shot(page, 'incident-detail');

    const acknowledge = page.getByRole('button', { name: /Acknowledge/ });
    await acknowledge.click();
    await expect(page.getByText('Incident acknowledged.')).toBeVisible();
    await expect(acknowledge).toHaveCount(0);
    await expect(header(page).getByText('Acknowledged', { exact: true })).toBeVisible();
    await expect(page.getByRole('list', { name: 'Incident timeline' })).toContainText(
      'Manual (API call, no verified identity)',
    );
    // There is deliberately no manual resolve.
    await expect(page.getByRole('button', { name: /resolve/i })).toHaveCount(0);
  });

  test('fix the service: the incident resolves by itself', async ({ page }) => {
    await page.goto(`/resources/${resourceId}`);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('URL to check').fill(`${DEMO_TARGET}/`);
    await dialog.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByText(`Saved “${name}”.`)).toBeVisible();

    await page.goto('/incidents?state=RESOLVED');
    await eventually(page, async () => {
      await expect(page.getByRole('link', { name: incidentTitle })).toBeVisible({ timeout: 3_000 });
    });
    await page.getByRole('link', { name: incidentTitle }).click();

    await expect(header(page).getByText('Resolved', { exact: true })).toBeVisible();
    await expect(page.getByText('Recovered automatically')).toBeVisible();
    await expect(page.getByRole('list', { name: 'Incident timeline' })).toContainText(
      'Recovered: resolved automatically',
    );
    await expect(page.getByRole('button', { name: /Acknowledge/ })).toHaveCount(0);
  });

  test('the overview lists the resolved incident', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Recently resolved' })).toBeVisible();
    await expect(page.getByRole('link', { name: incidentTitle })).toBeVisible();
    await shot(page, 'overview');
  });

  test('delete the resource after an explicit confirmation', async ({ page }) => {
    await page.goto(`/resources/${resourceId}`);
    await page.getByRole('button', { name: 'Delete', exact: true }).click();

    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(/all of its monitoring history/i)).toBeVisible();
    await expect(dialog.getByText(/incidents it caused are kept/i)).toBeVisible();
    await dialog.getByRole('button', { name: 'Delete resource' }).click();

    await expect(page).toHaveURL(/\/resources$/);
    await expect(page.getByText(`Deleted “${name}”.`)).toBeVisible();
    await page.getByLabel('Search by name').fill(name);
    await expect(page.getByText('No resources match these filters')).toBeVisible();

    // The API agrees, and the incident record survives with the resource marked as deleted.
    const gone = await page.request.get(`${API_URL}/api/v1/resources/${resourceId}`);
    expect(gone.status()).toBe(404);
    resourceId = null;

    await page.goto('/incidents?state=RESOLVED');
    const row = page.getByRole('row', { name: new RegExp(incidentTitle) });
    await expect(row).toBeVisible();
    await expect(row.getByText('(deleted)')).toBeVisible();
  });
});

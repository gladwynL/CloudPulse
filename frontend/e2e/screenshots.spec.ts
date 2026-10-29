import {
  API_URL,
  CHECK_INTERVAL,
  DEMO_TARGET,
  deleteResourceIfExists,
  expect,
  test,
} from './fixtures';

/**
 * Refreshes the documentation and showcase screenshots (`docs/screenshots/`) from the real
 * dashboard running against the real stack. Slow (about five minutes, so the charts have real
 * data in several one-minute buckets) and it writes files, so it only runs on request:
 *
 *   E2E_SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts
 *
 * It creates four disposable services (healthy, one that goes degraded and recovers, one that
 * stays healthy, and one that is unreachable) plus rules, changes only the URL of its own
 * resource, and deletes everything it created. All names, URLs and numbers are synthetic.
 */
test.describe('documentation screenshots', () => {
  test.skip(!process.env.E2E_SCREENSHOTS, 'set E2E_SCREENSHOTS=1 to refresh the screenshots');
  test.describe.configure({ timeout: 900_000 });
  // One consistent desktop size for every desktop capture.
  test.use({ viewport: { width: 1440, height: 900 } });

  const OUT = '../docs/screenshots';
  const PHASE_MS = 66_000; // a little over a minute so each phase fills its own 1-minute bucket

  test('capture the dashboard through a healthy, degraded and recovered service', async ({
    page,
    request,
  }) => {
    const created: string[] = [];
    const paymentsRule = 'Two failed checks in a row';

    const shot = (
      file: string,
      options: {
        fullPage?: boolean;
        clip?: { x: number; y: number; width: number; height: number };
      } = {},
    ) => page.screenshot({ path: `${OUT}/${file}.png`, animations: 'disabled', ...options });

    const api = async (path: string, data: unknown) => {
      const response = await request.post(`${API_URL}${path}`, { data });
      expect(response.status(), await response.text()).toBe(201);
      return (await response.json()) as { id: string };
    };

    try {
      // A healthy service created through the UI (so the form is exercised too).
      await page.goto('/resources');
      await page.getByRole('button', { name: 'Add resource' }).first().click();
      const dialog = page.getByRole('dialog');
      await dialog.getByLabel('Name', { exact: true }).fill('payments-api');
      await dialog.getByLabel('URL to check').fill(`${DEMO_TARGET}/`);
      await dialog.getByLabel('Check interval (seconds)').fill(CHECK_INTERVAL);
      await dialog.getByLabel('Timeout (seconds)').fill(CHECK_INTERVAL);
      await dialog.getByLabel('Description (optional)').fill('Card payments and checkout');
      await dialog.getByRole('button', { name: 'Add resource' }).click();
      await expect(page).toHaveURL(/\/resources\/[0-9a-f-]{36}$/);
      const paymentsId = page.url().split('/').pop() as string;
      created.push(paymentsId);

      // The rest of the synthetic fleet, through the API.
      const common = {
        check_interval_seconds: Number(CHECK_INTERVAL),
        timeout_seconds: Number(CHECK_INTERVAL),
      };
      const search = await api('/api/v1/resources', {
        name: 'search-service',
        url: `${DEMO_TARGET}/`,
        description: 'Product search',
        ...common,
      }); // fmt: skip
      const inventory = await api('/api/v1/resources', {
        name: 'inventory-service',
        url: `${DEMO_TARGET}/`,
        description: 'Stock levels',
        ...common,
      }); // fmt: skip
      const partner = await api('/api/v1/resources', {
        name: 'partner-gateway',
        url: 'http://partner-gateway.invalid/health',
        description: 'Third-party shipping gateway (unreachable)',
        ...common,
      }); // fmt: skip
      created.push(search.id, inventory.id, partner.id);

      await api(`/api/v1/resources/${partner.id}/alert-rules`, {
        name: 'Gateway down',
        rule_type: 'STATUS',
        status_condition: 'DOWN',
        severity: 'CRITICAL',
      }); // fmt: skip
      await api(`/api/v1/resources/${search.id}/alert-rules`, {
        name: 'Slow search responses',
        rule_type: 'LATENCY_THRESHOLD',
        operator: 'GREATER_THAN',
        threshold: 800,
        consecutive_failures: 3,
        severity: 'WARNING',
      }); // fmt: skip

      // The payments rule through the UI.
      await page.goto('/alerts');
      await page.getByRole('button', { name: 'Create rule' }).first().click();
      const rule = page.getByRole('dialog');
      await rule.getByLabel('Resource').selectOption({ label: 'payments-api' });
      await rule.getByLabel('Rule name').fill(paymentsRule);
      await rule.getByLabel(/Consecutive failed checks/).fill('2');
      await rule.getByRole('button', { name: 'Create rule' }).click();
      await expect(page.getByText(`Created rule “${paymentsRule}”.`)).toBeVisible();

      const setPaymentsUrl = async (url: string) => {
        await page.goto(`/resources/${paymentsId}`);
        await page.getByRole('button', { name: 'Edit', exact: true }).click();
        const edit = page.getByRole('dialog');
        await edit.getByLabel('URL to check').fill(url);
        await edit.getByRole('button', { name: 'Save changes' }).click();
        await expect(page.getByText('Saved “payments-api”.')).toBeVisible();
      };

      // Phase 1: healthy
      await page.waitForTimeout(PHASE_MS);

      // Phase 2: degraded (the demo target answers 404 for this path); an incident opens
      await setPaymentsUrl(`${DEMO_TARGET}/missing`);
      await page.waitForTimeout(PHASE_MS);
      await page.goto('/incidents');
      await expect(
        page.getByRole('link', { name: /Two failed checks in a row on payments-api/ }),
      ).toBeVisible({ timeout: 30_000 });
      await page.getByRole('link', { name: /Two failed checks in a row on payments-api/ }).click();
      await expect(page.getByRole('heading', { name: 'Timeline' })).toBeVisible();
      await page.waitForTimeout(500);
      await shot('incident-detail');

      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Active incidents' })).toBeVisible();
      await expect(
        page.getByRole('link', { name: /Gateway down on partner-gateway/ }).first(),
      ).toBeVisible({ timeout: 30_000 });
      await page.waitForTimeout(800);
      await shot('overview');

      // A tighter, larger composition for the showcase hero.
      await page.setViewportSize({ width: 1000, height: 700 });
      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Active incidents' })).toBeVisible();
      await page.waitForTimeout(800);
      await shot('overview-hero');
      await page.setViewportSize({ width: 1440, height: 900 });

      await page.goto('/alerts');
      await expect(page.getByRole('row', { name: new RegExp(paymentsRule) })).toBeVisible();
      await page.waitForTimeout(500);
      await shot('alert-rules');

      // Phase 3: recovered
      await setPaymentsUrl(`${DEMO_TARGET}/`);
      await page.waitForTimeout(PHASE_MS);
      // Summary, both charts and the top of the history: the page is long, the story is up here.
      // A taller viewport (not a clipped full-page capture) keeps the sidebar full height.
      await page.setViewportSize({ width: 1440, height: 1100 });
      await page.goto(`/resources/${paymentsId}?period=1h`);
      await expect(page.getByRole('img', { name: /Response time over time/ })).toBeVisible({
        timeout: 30_000,
      });
      await page.waitForTimeout(1_000);
      await shot('resource-detail');

      // The phone layout.
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible();
      await page.waitForTimeout(600);
      await shot('overview-mobile');
      await page.goto('/alerts');
      await expect(page.getByRole('heading', { name: 'Alert rules', level: 1 })).toBeVisible();
      await page.waitForTimeout(600);
      await shot('alert-rules-mobile', {
        fullPage: true,
        clip: { x: 0, y: 0, width: 390, height: 844 },
      });
    } finally {
      for (const id of created) await deleteResourceIfExists(request, id);
    }
  });
});

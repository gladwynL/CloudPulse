import { CHECK_INTERVAL, DEMO_TARGET, deleteResourceIfExists, expect, test } from './fixtures';

/**
 * Refreshes the documentation and showcase screenshots (`docs/screenshots/`) from the real
 * dashboard. It is slow (about four minutes, so the charts have real data in several time
 * buckets) and writes files, so it only runs on request:
 *
 *   E2E_SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts
 *
 * It creates one disposable resource and a rule, takes the service through healthy, degraded and
 * recovered phases by changing that resource's own URL, and deletes it at the end.
 */

const OUT = '../docs/screenshots';
const PHASE_MS = 66_000; // a little over one minute so each phase fills its own 1-minute bucket

test.describe('documentation screenshots', () => {
  test.skip(!process.env.E2E_SCREENSHOTS, 'set E2E_SCREENSHOTS=1 to refresh the screenshots');
  test.describe.configure({ timeout: 600_000 });

  test('capture the dashboard through a healthy, degraded and recovered service', async ({
    page,
    request,
  }) => {
    const name = `payments-api-${Date.now().toString(36).slice(-4)}`;
    const ruleName = 'Two failed checks in a row';
    let resourceId: string | null = null;

    const shot = (file: string, clip?: { x: number; y: number; width: number; height: number }) =>
      page.screenshot({
        path: `${OUT}/${file}.png`,
        fullPage: Boolean(clip),
        clip,
        animations: 'disabled',
      });

    const setUrl = async (url: string) => {
      await page.goto(`/resources/${resourceId}`);
      await page.getByRole('button', { name: 'Edit', exact: true }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByLabel('URL to check').fill(url);
      await dialog.getByRole('button', { name: 'Save changes' }).click();
      await expect(page.getByText(`Saved “${name}”.`)).toBeVisible();
    };

    try {
      // 1. A healthy service
      await page.goto('/resources');
      await page.getByRole('button', { name: 'Add resource' }).first().click();
      const dialog = page.getByRole('dialog');
      await dialog.getByLabel('Name', { exact: true }).fill(name);
      await dialog.getByLabel('URL to check').fill(`${DEMO_TARGET}/`);
      await dialog.getByLabel('Check interval (seconds)').fill(CHECK_INTERVAL);
      await dialog.getByLabel('Timeout (seconds)').fill(CHECK_INTERVAL);
      await dialog.getByLabel('Description (optional)').fill('Public checkout and payment API');
      await dialog.getByRole('button', { name: 'Add resource' }).click();
      await expect(page).toHaveURL(/\/resources\/[0-9a-f-]{36}$/);
      resourceId = page.url().split('/').pop() ?? null;

      await page.goto('/alerts');
      await page.getByRole('button', { name: 'Create rule' }).first().click();
      const rule = page.getByRole('dialog');
      await rule.getByLabel('Resource').selectOption({ label: name });
      await rule.getByLabel('Rule name').fill(ruleName);
      await rule.getByLabel(/Consecutive failed checks/).fill('2');
      await rule.getByRole('button', { name: 'Create rule' }).click();
      await expect(page.getByText(`Created rule “${ruleName}”.`)).toBeVisible();

      await page.waitForTimeout(PHASE_MS);

      // 2. Degraded: the demo target answers 404 for this path
      await setUrl(`${DEMO_TARGET}/missing`);
      await page.waitForTimeout(PHASE_MS);
      await page.goto('/incidents');
      await expect(page.getByRole('link', { name: new RegExp(name) }).first()).toBeVisible({
        timeout: 30_000,
      });
      await shot('incidents');
      await page
        .getByRole('link', { name: new RegExp(name) })
        .first()
        .click();
      await expect(page.getByRole('heading', { name: 'Timeline' })).toBeVisible();
      await shot('incident-detail');
      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Active incidents' })).toBeVisible();
      await expect(page.getByRole('link', { name: new RegExp(name) }).first()).toBeVisible();
      await shot('overview');

      // 3. Recovered
      await setUrl(`${DEMO_TARGET}/`);
      await page.waitForTimeout(PHASE_MS);
      await page.goto(`/resources/${resourceId}?period=1h`);
      await expect(page.getByRole('img', { name: /Response time over time/ })).toBeVisible({
        timeout: 30_000,
      });
      await page.waitForTimeout(1_000);
      // Summary, both charts and the top of the history: the page is long, the story is up here.
      await shot('resource-detail', { x: 0, y: 0, width: 1280, height: 1130 });

      await page.goto('/alerts');
      await expect(page.getByRole('row', { name: new RegExp(ruleName) })).toBeVisible();
      await shot('alert-rules');

      // A phone-sized view of the same overview.
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible();
      await page.waitForTimeout(500);
      await shot('overview-mobile');
    } finally {
      await deleteResourceIfExists(request, resourceId);
    }
  });
});

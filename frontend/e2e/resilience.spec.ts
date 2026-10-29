import { API_ROUTES, API_URL, expect, test } from './fixtures';

// These tests break the API on purpose, so the browser's own "failed to load" console errors are
// expected here. The no-external-requests audit still applies.
test.use({ auditConsole: false });

test.describe('when the backend is unavailable', () => {
  test('the dashboard explains an unreachable API and recovers on retry', async ({ page }) => {
    for (const pattern of API_ROUTES) {
      await page.route(pattern, (route) => route.abort('connectionrefused'));
    }
    await page.goto('/');

    await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible();
    await expect(page.getByText('Cannot reach the API')).toBeVisible();
    await expect(page.getByLabel(/^API unreachable/)).toBeVisible();

    // The shell survives and other pages degrade the same way instead of going blank.
    await page.getByRole('link', { name: 'Incidents' }).click();
    await expect(page.getByRole('heading', { name: 'Incidents', level: 1 })).toBeVisible();
    await expect(page.getByText('Cannot reach the API')).toBeVisible();

    for (const pattern of API_ROUTES) await page.unroute(pattern);
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByText('Cannot reach the API')).toHaveCount(0);
    await expect(page.getByLabel(/^API online/)).toBeVisible({ timeout: 20_000 });
  });

  test('a database outage (503) is reported distinctly from an unreachable API', async ({
    page,
  }) => {
    await page.route(`${API_URL}/api/v1/**`, (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'The database is unavailable' }),
      }),
    );
    await page.goto('/resources');
    await expect(page.getByText('Service unavailable')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('The database is unavailable')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
  });

  test('an unknown resource id is a clear not-found page, not a crash', async ({ page }) => {
    await page.goto('/resources/00000000-0000-0000-0000-000000000000');
    await expect(page.getByText('Resource not found')).toBeVisible();
    await page.goto('/incidents/00000000-0000-0000-0000-000000000000');
    await expect(page.getByText('Incident not found')).toBeVisible();
    await page.goto('/this/route/does/not/exist');
    await expect(page.getByText('Page not found')).toBeVisible();
  });
});

import { createResourceViaApi, deleteResourceIfExists, expect, test, uniqueName } from './fixtures';

const VIEWPORTS = [
  { name: 'phone 375', width: 375, height: 812 },
  { name: 'phone 430', width: 430, height: 932 },
  { name: 'tablet 768', width: 768, height: 1024 },
  { name: 'laptop 1024', width: 1024, height: 768 },
  { name: 'desktop 1440', width: 1440, height: 900 },
  { name: 'wide 1920', width: 1920, height: 1080 },
];

test.describe('responsive layout', () => {
  let resourceId: string;

  test.beforeAll(async ({ request }) => {
    // A disposable resource so the detail page, with its tables and charts, is covered too.
    resourceId = await createResourceViaApi(request, uniqueName('layout'));
  });

  test.afterAll(async ({ request }) => {
    await deleteResourceIfExists(request, resourceId);
  });

  for (const viewport of VIEWPORTS) {
    test(`no horizontal page overflow at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const routes = ['/', '/resources', '/alerts', '/incidents', `/resources/${resourceId}`];

      for (const route of routes) {
        await page.goto(route);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        // Let the data and charts settle.
        await page.waitForLoadState('networkidle');

        const { scrollWidth, clientWidth } = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        }));
        expect(
          scrollWidth,
          `${route} overflows horizontally at ${viewport.width}px`,
        ).toBeLessThanOrEqual(clientWidth);
      }
    });
  }

  test('the phone navigation drawer opens, traps focus in the page and closes', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/');

    const toggle = page.getByRole('button', { name: 'Open navigation' });
    await toggle.click();
    await expect(page.getByRole('button', { name: 'Close navigation' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    await page
      .getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Resources' })
      .click();
    await expect(page.getByRole('heading', { name: 'Resources', level: 1 })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  test('dialogs are usable on a phone and close with Escape', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/resources');
    await page.getByRole('button', { name: 'Add resource' }).first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    const box = await dialog.boundingBox();
    expect(box?.width ?? 0).toBeLessThanOrEqual(375);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  });

  test('keyboard users can reach the main content and see a visible focus ring', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to content' });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('main')).toBeFocused();
  });

  test('animations are removed for people who prefer reduced motion', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');
    const durations = await page.evaluate(() =>
      [...document.querySelectorAll('.spinner, .btn')].map(
        (el) => getComputedStyle(el).animationDuration,
      ),
    );
    for (const duration of durations) {
      // Reduced motion shortens every animation to (almost) nothing.
      expect(parseFloat(duration)).toBeLessThanOrEqual(0.01);
    }
  });
});

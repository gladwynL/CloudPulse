import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import {
  API_URL,
  DEMO_TARGET,
  createResourceViaApi,
  deleteResourceIfExists,
  expect,
  test,
  uniqueName,
} from './fixtures';

/**
 * Automated WCAG 2.1 A/AA checks (axe-core) over every page, both dialogs and the phone layout,
 * against real data: a healthy service, a degraded one with a firing rule, and its incident.
 * Automated checks catch only part of accessibility; keyboard, focus and dialog behaviour are
 * asserted explicitly below and in layout.spec.ts, and the journeys were also reviewed by hand.
 */
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

/** Violations are collected across a whole test so one run lists every problem. */
async function violationsOf(page: Page, label: string): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return results.violations.map((v) => {
    const where = v.nodes.map((n) => n.target.join(' ')).slice(0, 3);
    return `[${label}] ${v.id} (${v.impact}): ${v.help} :: ${where.join(' | ')}`;
  });
}

async function audit(page: Page, label: string) {
  expect(await violationsOf(page, label), `accessibility violations on ${label}`).toEqual([]);
}

test.describe('accessibility (axe-core, WCAG 2.1 A/AA)', () => {
  test.describe.configure({ timeout: 180_000 });
  let healthyId = '';
  let degradedId = '';
  let incidentId = '';

  test.beforeAll(async ({ request }) => {
    healthyId = await createResourceViaApi(request, uniqueName('a11y-ok'));
    degradedId = await createResourceViaApi(
      request,
      uniqueName('a11y-bad'),
      `${DEMO_TARGET}/missing`,
    );
    const rule = await request.post(`${API_URL}/api/v1/resources/${degradedId}/alert-rules`, {
      data: {
        name: 'Two unhealthy checks',
        rule_type: 'CONSECUTIVE_FAILURES',
        consecutive_failures: 2,
      },
    });
    expect(rule.status()).toBe(201);
    // Wait for the incident to open so the incident pages have real content.
    await expect
      .poll(
        async () => {
          const response = await request.get(
            `${API_URL}/api/v1/incidents?resource_id=${degradedId}&limit=1`,
          );
          incidentId = (await response.json()).items?.[0]?.id ?? '';
          return incidentId;
        },
        { timeout: 90_000, intervals: [2_000] },
      )
      .not.toBe('');
    // A few healthy checks and some history for the charts and tables.
    await new Promise((resolve) => setTimeout(resolve, 15_000));
  });

  test.afterAll(async ({ request }) => {
    await deleteResourceIfExists(request, healthyId);
    await deleteResourceIfExists(request, degradedId);
  });

  for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'phone', width: 375, height: 812 },
  ]) {
    test(`every page has no WCAG A/AA violations (${viewport.name})`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const routes: [string, string][] = [
        ['overview', '/'],
        ['resources', '/resources'],
        ['resource detail', `/resources/${healthyId}?period=1h`],
        ['degraded resource detail', `/resources/${degradedId}?period=1h`],
        ['alert rules', '/alerts'],
        ['incidents', '/incidents'],
        ['incident detail', `/incidents/${incidentId}`],
        ['not found', '/no/such/page'],
      ];
      const found: string[] = [];
      for (const [label, route] of routes) {
        await page.goto(route);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        await page.waitForLoadState('networkidle');
        found.push(...(await violationsOf(page, `${label} (${viewport.name})`)));
      }
      expect(found, 'accessibility violations').toEqual([]);
    });
  }

  test('the add-resource and create-rule dialogs have no violations and manage focus', async ({
    page,
  }) => {
    await page.goto('/resources');
    const opener = page.getByRole('button', { name: 'Add resource' }).first();
    await opener.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    // A labelled dialog whose first field has focus.
    await expect(dialog).toHaveAccessibleName(/Add resource/);
    await expect(dialog.getByLabel('Name', { exact: true })).toBeFocused();
    await audit(page, 'add resource dialog');

    // Validation errors are announced and tied to their fields.
    await dialog.getByRole('button', { name: 'Add resource' }).click();
    const error = dialog.getByText('Enter a name.');
    await expect(error).toBeVisible();
    await expect(error).toHaveAttribute('role', 'alert');
    await expect(dialog.getByLabel('Name', { exact: true })).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    await audit(page, 'add resource dialog with errors');

    // Escape closes it and focus returns to the control that opened it.
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();

    await page.goto('/alerts');
    await page.getByRole('button', { name: 'Create rule' }).first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await audit(page, 'create rule dialog');
  });

  test('status is never conveyed by colour alone', async ({ page }) => {
    await page.goto('/resources');
    await expect(page.getByRole('table', { name: 'Monitored resources' })).toBeVisible();
    // Every badge carries a text label and a hidden decorative icon.
    const badges = page.locator('.badge');
    const count = await badges.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i++) {
      const badge = badges.nth(i);
      expect((await badge.innerText()).trim().length).toBeGreaterThan(0);
      await expect(badge.locator('svg').first()).toHaveAttribute('aria-hidden', 'true');
    }
  });

  test('interactive controls meet a 24px minimum target size on touch layouts', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    for (const route of ['/', '/resources', '/alerts', `/incidents/${incidentId}`]) {
      await page.goto(route);
      await page.waitForLoadState('networkidle');
      const small = await page.evaluate(() =>
        [
          ...document.querySelectorAll<HTMLElement>(
            'button, [role="button"], input, select, textarea',
          ),
        ]
          .filter((el) => el.offsetParent !== null)
          .map((el) => ({ el, r: el.getBoundingClientRect() }))
          .filter(({ r }) => r.width > 0 && (r.width < 24 || r.height < 24))
          .map(
            ({ el }) =>
              `${el.tagName.toLowerCase()} "${el.getAttribute('aria-label') ?? el.textContent?.trim()}"`,
          ),
      );
      expect(small, `controls under 24x24 CSS px on ${route}`).toEqual([]);
    }
  });

  test('the page is usable at 200% text size without losing content or scrolling sideways', async ({
    page,
  }) => {
    for (const route of ['/', '/resources', '/alerts', `/incidents/${incidentId}`]) {
      await page.goto(route);
      await page.addStyleTag({ content: 'html { font-size: 200% !important; }' });
      await page.waitForLoadState('networkidle');
      const { scrollWidth, clientWidth, offenders } = await page.evaluate(() => {
        const width = document.documentElement.clientWidth;
        return {
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: width,
          offenders: [...document.querySelectorAll('body *')]
            .filter((el) => el.getBoundingClientRect().right > width + 0.5)
            .slice(0, 5)
            .map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)}`),
        };
      });
      expect(
        scrollWidth,
        `${route} overflows at 200% text: ${offenders.join(', ')}`,
      ).toBeLessThanOrEqual(clientWidth);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    }
  });
});

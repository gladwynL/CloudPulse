import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, statSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { extname, join, normalize } from 'node:path';
import { expect, test } from '@playwright/test';

/**
 * The static showcase, built by scripts/build_site.py and served under /CloudPulse/ exactly like a
 * GitHub Pages project site. It needs no backend, so it runs without the Docker stack.
 */
const PREFIX = '/CloudPulse/';
const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
};

let server: Server;
let origin = '';
const requested: string[] = [];

test.beforeAll(async () => {
  const out = join(mkdtempSync(join(tmpdir(), 'cloudpulse-site-')), '_site');
  execFileSync('python', ['scripts/build_site.py', '--out', out], { cwd: '..', stdio: 'pipe' });

  server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://x');
    requested.push(url.pathname);
    if (!url.pathname.startsWith(PREFIX)) {
      res.writeHead(404).end('not under the project subpath');
      return;
    }
    let file = normalize(join(out, url.pathname.slice(PREFIX.length) || 'index.html'));
    if (!file.startsWith(out)) {
      res.writeHead(403).end();
      return;
    }
    try {
      if (statSync(file).isDirectory()) file = join(file, 'index.html');
      res.writeHead(200, { 'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream' });
      res.end(readFileSync(file));
    } catch {
      res.writeHead(404).end('missing');
    }
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

test.afterAll(async () => {
  await new Promise((resolve) => server.close(resolve));
});

test.describe('static showcase under /CloudPulse/', () => {
  test('loads with every image, no console errors and no requests outside its own files', async ({
    page,
  }) => {
    const errors: string[] = [];
    const foreign: string[] = [];
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
    page.on('pageerror', (e) => errors.push(e.message));
    page.on('request', (r) => {
      if (!r.url().startsWith(origin + PREFIX)) foreign.push(r.url());
    });

    await page.goto(`${origin}${PREFIX}`);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    // Scroll everything into view so lazy images load, then check each really decoded.
    await page.evaluate(async () => {
      for (const img of document.images) img.loading = 'eager';
      await Promise.all([...document.images].map((img) => img.decode().catch(() => undefined)));
    });
    const broken = await page.evaluate(() =>
      [...document.images].filter((i) => !i.complete || i.naturalWidth === 0).map((i) => i.src),
    );
    expect(broken).toEqual([]);
    expect(await page.locator('img').count()).toBeGreaterThanOrEqual(5);
    expect(errors).toEqual([]);
    expect(foreign).toEqual([]);
  });

  test('says plainly that it is static and the backend is not publicly hosted', async ({
    page,
  }) => {
    await page.goto(`${origin}${PREFIX}`);
    await expect(
      page.getByRole('heading', { name: 'This page is a static showcase' }),
    ).toBeVisible();
    await expect(page.getByText(/not hosted publicly/i)).toBeVisible();
  });

  test('in-page navigation works and external links point only at the GitHub project', async ({
    page,
  }) => {
    await page.goto(`${origin}${PREFIX}`);
    await page
      .getByRole('navigation', { name: 'Sections' })
      .getByRole('link', { name: 'Run it' })
      .click();
    await expect(page).toHaveURL(/#run$/);
    await expect(page.getByRole('heading', { name: 'Run the real thing' })).toBeInViewport();

    const hrefs = await page
      .locator('a[href^="http"]')
      .evaluateAll((links) => links.map((a) => (a as HTMLAnchorElement).href));
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      expect(href.startsWith('https://github.com/gladwynL/CloudPulse')).toBe(true);
    }
  });

  test('is a script-free page with a locked-down content security policy', async ({ page }) => {
    await page.goto(`${origin}${PREFIX}`);
    expect(await page.locator('script').count()).toBe(0);
    const csp = await page
      .locator('meta[http-equiv="Content-Security-Policy"]')
      .getAttribute('content');
    expect(csp).toContain("default-src 'none'");
    expect(csp).not.toMatch(/connect-src|script-src/);
  });

  for (const width of [375, 768, 1440]) {
    test(`has no horizontal overflow at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${origin}${PREFIX}`);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      const { scrollWidth, clientWidth } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
    });
  }

  test('the skip link and keyboard focus work', async ({ page }) => {
    await page.goto(`${origin}${PREFIX}`);
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  });
});

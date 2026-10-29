import { expect, test as base, type APIRequestContext } from '@playwright/test';

export const API_URL = process.env.E2E_API_URL ?? 'http://localhost:8000';
export const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:3000';

/**
 * The API's routes, as route patterns. Listed individually (rather than `${API_URL}/**`) because in
 * the hardened stack the API shares the dashboard's origin, and blocking everything would block
 * the page itself.
 */
export const API_ROUTES = [`${API_URL}/api/v1/**`, `${API_URL}/health`, `${API_URL}/ready`];

/** Where the demo target is reachable from the worker (inside the Compose network). */
export const DEMO_TARGET = process.env.E2E_TARGET_URL ?? 'http://demo-target:8080';
export const CHECK_INTERVAL = process.env.E2E_CHECK_INTERVAL ?? '5';

/** A unique, recognisable name so a test only ever touches what it created. */
export function uniqueName(prefix: string): string {
  return `e2e-${prefix}-${Date.now().toString(36)}`;
}

interface Options {
  /** Fail on browser console errors. Off for tests that deliberately break the API. */
  auditConsole: boolean;
}

/**
 * Every test runs with an audit: no uncaught page errors, no console errors (unless a test opts
 * out), and no request leaving for any host except the dashboard and the API.
 */
export const test = base.extend<Options & { audit: void }>({
  auditConsole: [true, { option: true }],
  audit: [
    async ({ page, auditConsole }, use) => {
      // Opt-in robustness check: `E2E_FONT_STRESS=1` swaps in a wide font. CI runs on Linux, whose
      // fallback fonts are wider than a developer's Windows or macOS ones, and that difference once
      // hid overflow and keyboard-access bugs locally. This reproduces it without a Linux machine.
      if (process.env.E2E_FONT_STRESS) {
        await page.addInitScript(() => {
          document.addEventListener('DOMContentLoaded', () => {
            const style = document.createElement('style');
            style.textContent = '* { font-family: Verdana, "DejaVu Sans", sans-serif !important; }';
            document.head.appendChild(style);
          });
        });
      }
      const consoleErrors: string[] = [];
      const pageErrors: string[] = [];
      const external: string[] = [];
      const allowed = new Set([new URL(BASE_URL).host, new URL(API_URL).host]);

      page.on('console', (message) => {
        if (message.type() === 'error') consoleErrors.push(message.text());
      });
      page.on('pageerror', (error) => pageErrors.push(error.message));
      page.on('request', (request) => {
        const url = new URL(request.url());
        if (url.protocol.startsWith('http') && !allowed.has(url.host)) {
          external.push(`${request.method()} ${url.origin}`);
        }
      });

      await use();

      expect(pageErrors, 'uncaught page errors').toEqual([]);
      expect(external, 'requests to hosts other than the dashboard and the API').toEqual([]);
      if (auditConsole) expect(consoleErrors, 'browser console errors').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** Delete a test-created resource through the API (cleanup only; safe if it is already gone). */
export async function deleteResourceIfExists(request: APIRequestContext, id: string | null) {
  if (!id) return;
  const response = await request.delete(`${API_URL}/api/v1/resources/${id}`);
  if (![204, 404].includes(response.status())) {
    throw new Error(`cleanup of ${id} failed with ${response.status()}`);
  }
}

/** Create a throwaway resource through the API (for specs that are not about creating one). */
export async function createResourceViaApi(
  request: APIRequestContext,
  name: string,
  url = `${DEMO_TARGET}/`,
): Promise<string> {
  const response = await request.post(`${API_URL}/api/v1/resources`, {
    data: {
      name,
      url,
      check_interval_seconds: Number(CHECK_INTERVAL),
      timeout_seconds: Number(CHECK_INTERVAL),
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()).id as string;
}

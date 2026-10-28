import { defineConfig, devices } from '@playwright/test';

/**
 * Browser end-to-end tests run against the real Docker stack (frontend, API, worker, database and
 * the demo target), not mocks. Start it first:
 *
 *   MONITORING_ALLOWED_LOCAL_HOSTS=demo-target:8080 MONITORING_MIN_INTERVAL_SECONDS=5 \
 *     docker compose --profile demo up --build --detach --wait
 *
 * Tests only create, change and delete resources they created themselves (named `e2e-…`).
 */
export default defineConfig({
  testDir: './e2e',
  // The workflow spec is one ordered scenario; specs share a database, so run one at a time.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  outputDir: 'test-results',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // A fixed locale and zone keep text assertions and screenshots stable.
    locale: 'en-US',
    timezoneId: 'UTC',
    colorScheme: 'dark',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});

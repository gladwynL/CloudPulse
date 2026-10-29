import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  // Read VITE_* variables from the repo-root .env shared with Docker Compose.
  envDir: '..',
  server: {
    port: 5173,
    strictPort: true,
  },
  preview: {
    port: 4173,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
    // The first import of the chart library is slow when the whole suite runs in parallel (cold
    // module transforms), so tests get generous budgets. A genuinely broken test still fails; it
    // just does not fail because a CI machine was busy.
    testTimeout: 30_000,
    // Deterministic tests: a fixed API origin and no background polling.
    env: {
      VITE_API_URL: 'http://api.test',
      VITE_APP_ENV: 'test',
      VITE_POLL_OVERVIEW_MS: '0',
      VITE_POLL_RESOURCES_MS: '0',
      VITE_POLL_INCIDENTS_MS: '0',
      VITE_POLL_RULES_MS: '0',
      VITE_POLL_CHARTS_MS: '0',
      VITE_POLL_HISTORY_MS: '0',
      VITE_POLL_SYSTEM_MS: '0',
    },
    // Playwright specs live in e2e/ and run under their own runner.
    exclude: ['node_modules', 'dist', 'e2e/**'],
  },
});

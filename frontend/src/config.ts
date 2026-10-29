const DEFAULT_API_URL = 'http://localhost:8000';

/**
 * `VITE_API_URL` is the API origin as the *browser* sees it, fixed at build time. The special value
 * `same-origin` sends API calls to whatever origin served the page; the hardened deployment uses it
 * because nginx proxies `/api`, `/health` and `/ready` to the backend over the internal network.
 */
function resolveApiUrl(raw: string | undefined): string {
  const value = (raw || DEFAULT_API_URL).trim();
  if (value === 'same-origin') return '';
  return value.replace(/\/+$/, '');
}

export const config = {
  apiUrl: resolveApiUrl(import.meta.env.VITE_API_URL),
  environment: import.meta.env.VITE_APP_ENV || 'local',
} as const;

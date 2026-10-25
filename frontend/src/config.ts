const DEFAULT_API_URL = 'http://localhost:8000';

export const config = {
  apiUrl: (import.meta.env.VITE_API_URL || DEFAULT_API_URL).replace(/\/+$/, ''),
  environment: import.meta.env.VITE_APP_ENV || 'local',
} as const;

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ToastProvider } from '../components/ui/Toast.tsx';

interface Options {
  /** The URL the router starts at. */
  route?: string;
  /** The route pattern the element is mounted on (needed for `:id` params). */
  path?: string;
}

/** Fresh query cache per test, no retries or caching surprises, with toasts and a router. */
export function renderWithProviders(
  ui: ReactElement,
  { route = '/', path = '*' }: Options = {},
): RenderResult & { client: QueryClient } {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const result = render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route path={path} element={ui} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  return Object.assign(result, { client });
}

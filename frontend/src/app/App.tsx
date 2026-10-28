import { QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { BrowserRouter } from 'react-router-dom';
import { ErrorBoundary } from '../components/layout/ErrorBoundary.tsx';
import { ToastProvider } from '../components/ui/Toast.tsx';
import { createQueryClient } from '../lib/queryClient.ts';
import { AppRoutes } from './routes.tsx';

export default function App() {
  // One client per app instance (created once, not on every render).
  const [queryClient] = useState(createQueryClient);
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
        </ToastProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

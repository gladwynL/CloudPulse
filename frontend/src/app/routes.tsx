import { lazy, Suspense } from 'react';
import { Link, Route, Routes } from 'react-router-dom';
import { AppShell } from '../components/layout/AppShell.tsx';
import { Button } from '../components/ui/Button.tsx';
import { LoadingState, NotFoundState } from '../components/ui/States.tsx';

// Route-level code splitting keeps the first load small; the chart library only loads with the
// resource detail page.
const OverviewPage = lazy(() =>
  import('../features/overview/OverviewPage.tsx').then((m) => ({ default: m.OverviewPage })),
);
const ResourcesPage = lazy(() =>
  import('../features/resources/ResourcesPage.tsx').then((m) => ({ default: m.ResourcesPage })),
);
const ResourceDetailPage = lazy(() =>
  import('../features/resources/ResourceDetailPage.tsx').then((m) => ({
    default: m.ResourceDetailPage,
  })),
);
const AlertsPage = lazy(() =>
  import('../features/alerts/AlertsPage.tsx').then((m) => ({ default: m.AlertsPage })),
);
const IncidentsPage = lazy(() =>
  import('../features/incidents/IncidentsPage.tsx').then((m) => ({ default: m.IncidentsPage })),
);
const IncidentDetailPage = lazy(() =>
  import('../features/incidents/IncidentDetailPage.tsx').then((m) => ({
    default: m.IncidentDetailPage,
  })),
);

function PageNotFound() {
  return (
    <NotFoundState
      what="Page"
      action={
        <Link to="/">
          <Button variant="primary">Back to overview</Button>
        </Link>
      }
    />
  );
}

export function AppRoutes() {
  return (
    <Suspense fallback={<LoadingState label="Loading page…" />}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          <Route path="resources" element={<ResourcesPage />} />
          <Route path="resources/:id" element={<ResourceDetailPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="incidents" element={<IncidentsPage />} />
          <Route path="incidents/:id" element={<IncidentDetailPage />} />
          <Route path="*" element={<PageNotFound />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

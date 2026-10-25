import { useCallback, useEffect, useRef, useState } from 'react';
import { probe, type ProbeResult } from '../lib/api.ts';

export interface SystemStatus {
  api: ProbeResult | null;
  database: ProbeResult | null;
  checkedAt: Date | null;
  loading: boolean;
}

const INITIAL: SystemStatus = { api: null, database: null, checkedAt: null, loading: true };

/** Query the API liveness and readiness probes; `refresh` re-runs both. */
export function useSystemStatus(): SystemStatus & { refresh: () => void } {
  const [status, setStatus] = useState<SystemStatus>(INITIAL);
  const controllerRef = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setStatus((previous) => ({ ...previous, loading: true }));
    try {
      const [api, database] = await Promise.all([
        probe('/health', controller.signal),
        probe('/ready', controller.signal),
      ]);
      setStatus({ api, database, checkedAt: new Date(), loading: false });
    } catch {
      // Aborted by a newer refresh or unmount; the newer run owns the state.
    }
  }, []);

  useEffect(() => {
    // Initial probe on mount; state updates happen after the awaited fetches resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void run();
    return () => controllerRef.current?.abort();
  }, [run]);

  return { ...status, refresh: () => void run() };
}

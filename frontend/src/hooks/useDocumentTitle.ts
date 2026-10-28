import { useEffect } from 'react';

/** Sets a per-page tab title: "<page> · CloudPulse". */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · CloudPulse`;
  }, [title]);
}

import { CircleAlert, CircleCheck, Info, X } from 'lucide-react';
import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
import { Button } from './Button.tsx';
import { ToastContext, type ToastApi, type ToastTone } from './useToast.ts';

interface ToastItem {
  id: number;
  tone: ToastTone;
  message: string;
}

const TONE_CLASS: Record<ToastTone, string> = {
  success: 'tone-healthy',
  error: 'tone-down',
  info: 'tone-info',
};

const ICONS = { success: CircleCheck, error: CircleAlert, info: Info };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (tone: ToastTone, message: string) => {
      const id = nextId.current++;
      setToasts((current) => [...current.slice(-3), { id, tone, message }]);
      // Errors stay longer; none steals focus.
      window.setTimeout(() => dismiss(id), tone === 'error' ? 10_000 : 5_000);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      success: (m) => push('success', m),
      error: (m) => push('error', m),
      info: (m) => push('info', m),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-region" role="region" aria-label="Notifications" aria-live="polite">
        {toasts.map((toast) => {
          const Icon = ICONS[toast.tone];
          return (
            <div key={toast.id} className={`toast ${TONE_CLASS[toast.tone]}`}>
              <Icon size={18} aria-hidden="true" />
              <p className="toast__body">{toast.message}</p>
              <Button
                variant="ghost"
                small
                iconOnly
                aria-label="Dismiss notification"
                onClick={() => dismiss(toast.id)}
              >
                <X size={14} aria-hidden="true" />
              </Button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

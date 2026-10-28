import {
  CircleAlert,
  CircleCheck,
  Info,
  SearchX,
  ServerCrash,
  TriangleAlert,
  WifiOff,
  type LucideIcon,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { ApiError, errorMessage } from '../../lib/api/client.ts';
import { Button } from './Button.tsx';

/** Initial load. `role="status"` announces it to assistive technology. */
export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state state--compact" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span className="muted">{label}</span>
    </div>
  );
}

export function EmptyState({
  icon: Icon = Info,
  title,
  children,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="state">
      <Icon className="state__icon" size={28} aria-hidden="true" />
      <p className="state__title">{title}</p>
      {children && <div className="state__body">{children}</div>}
      {action}
    </div>
  );
}

const ERROR_TITLES: Record<string, string> = {
  network: 'Cannot reach the API',
  timeout: 'The API is not responding',
  unavailable: 'Service unavailable',
  not_found: 'Not found',
  server: 'Server error',
  conflict: 'Action not allowed',
  validation: 'Invalid request',
  http: 'Request failed',
};

/** A failed fetch. Always offers a retry; never a blank screen. */
export function ErrorState({
  error,
  onRetry,
  title,
  retrying,
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  retrying?: boolean;
}) {
  const kind = error instanceof ApiError ? error.kind : 'http';
  const Icon = kind === 'network' || kind === 'timeout' ? WifiOff : ServerCrash;
  return (
    <div className="state state--error" role="alert">
      <Icon className="state__icon" size={28} aria-hidden="true" />
      <p className="state__title">{title ?? ERROR_TITLES[kind]}</p>
      <p className="state__body">{errorMessage(error)}</p>
      {onRetry && (
        <Button onClick={onRetry} loading={retrying}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function NotFoundState({ what, action }: { what: string; action?: ReactNode }) {
  return (
    <EmptyState icon={SearchX} title={`${what} not found`} action={action}>
      It may have been deleted, or the link may be wrong.
    </EmptyState>
  );
}

type NoticeTone = 'info' | 'warning' | 'error' | 'success';

const NOTICE_ICONS: Record<NoticeTone, LucideIcon> = {
  info: Info,
  warning: TriangleAlert,
  error: CircleAlert,
  success: CircleCheck,
};

const NOTICE_TONES: Record<NoticeTone, string> = {
  info: 'info',
  warning: 'degraded',
  error: 'down',
  success: 'healthy',
};

export function Notice({
  tone = 'info',
  title,
  children,
}: {
  tone?: NoticeTone;
  title?: string;
  children: ReactNode;
}) {
  const Icon = NOTICE_ICONS[tone];
  return (
    <div
      className={`notice tone-${NOTICE_TONES[tone]}`}
      role={tone === 'error' ? 'alert' : undefined}
    >
      <Icon size={18} aria-hidden="true" />
      <div>
        {title && <p className="notice__title">{title}</p>}
        <div>{children}</div>
      </div>
    </div>
  );
}

/** Small "updating" hint shown during background refetches. */
export function Refreshing({ active }: { active: boolean }) {
  return (
    <span className="refreshing" role="status" aria-live="polite">
      {active && (
        <>
          <span className="spinner" aria-hidden="true" /> Updating…
        </>
      )}
    </span>
  );
}

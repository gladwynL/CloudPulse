import {
  CheckCheck,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleX,
  Eye,
  Flame,
  OctagonAlert,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import type {
  AlertSeverity,
  AlertState,
  CheckStatus,
  IncidentStatus,
  ResourceStatus,
} from '../../lib/api/types.ts';

type Tone = 'healthy' | 'degraded' | 'down' | 'unknown' | 'info';

/**
 * Every status is conveyed by an icon, a text label *and* a colour; colour alone is never the
 * only signal.
 */
export function Badge({
  tone,
  icon: Icon,
  children,
  dashed,
}: {
  tone: Tone;
  icon: LucideIcon;
  children: string;
  dashed?: boolean;
}) {
  return (
    <span className={`badge tone-${tone}${dashed ? ' badge--dashed' : ''}`}>
      <Icon size={13} aria-hidden="true" strokeWidth={2.4} />
      {children}
    </span>
  );
}

const RESOURCE: Record<ResourceStatus, { tone: Tone; icon: LucideIcon; label: string }> = {
  HEALTHY: { tone: 'healthy', icon: CircleCheck, label: 'Healthy' },
  DEGRADED: { tone: 'degraded', icon: TriangleAlert, label: 'Degraded' },
  DOWN: { tone: 'down', icon: CircleX, label: 'Down' },
  UNKNOWN: { tone: 'unknown', icon: CircleHelp, label: 'Unknown' },
};

export function StatusBadge({ status }: { status: ResourceStatus | CheckStatus }) {
  const { tone, icon, label } = RESOURCE[status];
  return (
    <Badge tone={tone} icon={icon} dashed={status === 'UNKNOWN'}>
      {label}
    </Badge>
  );
}

const STATE: Record<AlertState, { tone: Tone; icon: LucideIcon; label: string }> = {
  OK: { tone: 'healthy', icon: CircleCheck, label: 'OK' },
  FIRING: { tone: 'down', icon: Flame, label: 'Firing' },
  INSUFFICIENT_DATA: { tone: 'unknown', icon: CircleDashed, label: 'Insufficient data' },
};

export function AlertStateBadge({ state }: { state: AlertState }) {
  const { tone, icon, label } = STATE[state];
  return (
    <Badge tone={tone} icon={icon} dashed={state === 'INSUFFICIENT_DATA'}>
      {label}
    </Badge>
  );
}

const INCIDENT: Record<IncidentStatus, { tone: Tone; icon: LucideIcon; label: string }> = {
  OPEN: { tone: 'down', icon: CircleAlert, label: 'Open' },
  ACKNOWLEDGED: { tone: 'degraded', icon: Eye, label: 'Acknowledged' },
  RESOLVED: { tone: 'healthy', icon: CheckCheck, label: 'Resolved' },
};

export function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  const { tone, icon, label } = INCIDENT[status];
  return (
    <Badge tone={tone} icon={icon}>
      {label}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  return severity === 'CRITICAL' ? (
    <Badge tone="down" icon={OctagonAlert}>
      Critical
    </Badge>
  ) : (
    <Badge tone="degraded" icon={TriangleAlert}>
      Warning
    </Badge>
  );
}

export function EnabledBadge({ enabled }: { enabled: boolean }) {
  return enabled ? (
    <Badge tone="info" icon={CircleCheck}>
      Enabled
    </Badge>
  ) : (
    <Badge tone="unknown" icon={CircleDashed} dashed>
      Disabled
    </Badge>
  );
}

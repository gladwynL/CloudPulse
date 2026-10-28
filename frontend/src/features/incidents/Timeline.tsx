import {
  CheckCheck,
  CircleAlert,
  Eye,
  Pause,
  Settings2,
  Trash2,
  type LucideIcon,
} from 'lucide-react';
import type { IncidentEvent, IncidentEventType } from '../../lib/api/types.ts';
import { DateTime } from '../../components/ui/Time.tsx';
import { actorLabel } from '../../lib/utils/rules.ts';

const EVENTS: Record<IncidentEventType, { icon: LucideIcon; tone: string; title: string }> = {
  OPENED: { icon: CircleAlert, tone: 'down', title: 'Incident opened' },
  ACKNOWLEDGED: { icon: Eye, tone: 'degraded', title: 'Acknowledged' },
  RECOVERED: { icon: CheckCheck, tone: 'healthy', title: 'Recovered: resolved automatically' },
  RULE_DISABLED: { icon: Pause, tone: 'unknown', title: 'Resolved: the rule was disabled' },
  RULE_RECONFIGURED: {
    icon: Settings2,
    tone: 'unknown',
    title: 'Resolved: the rule was reconfigured',
  },
  RULE_DELETED: { icon: Trash2, tone: 'unknown', title: 'Resolved: the rule was deleted' },
  RESOURCE_DISABLED: { icon: Pause, tone: 'unknown', title: 'Resolved: monitoring was disabled' },
  RESOURCE_DELETED: { icon: Trash2, tone: 'unknown', title: 'Resolved: the resource was deleted' },
};

const SHOWN_DETAILS: Record<string, string> = {
  observed_status: 'Observed status',
  observed_response_time_ms: 'Response time (ms)',
  failure_streak: 'Consecutive matches',
  recovery_successes: 'Recovery checks required',
  rule_type: 'Rule type',
};

function detailPairs(details: Record<string, unknown>): [string, string][] {
  return Object.entries(SHOWN_DETAILS)
    .filter(([key]) => details[key] !== undefined && details[key] !== null)
    .map(([key, label]) => [label, String(details[key])]);
}

export function Timeline({ events }: { events: IncidentEvent[] }) {
  return (
    <ol className="timeline" aria-label="Incident timeline">
      {events.map((event) => {
        const { icon: Icon, tone, title } = EVENTS[event.event_type];
        const pairs = detailPairs(event.details);
        return (
          <li key={event.id} className={`timeline__item tone-${tone}`}>
            <span className="timeline__dot" aria-hidden="true">
              <Icon size={12} />
            </span>
            <span className="timeline__title">{title}</span>
            <span className="timeline__meta">
              <DateTime iso={event.occurred_at} /> · {actorLabel(event.actor)}
            </span>
            {pairs.length > 0 && (
              <span className="timeline__meta">
                {pairs.map(([label, value]) => `${label}: ${value}`).join(' · ')}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

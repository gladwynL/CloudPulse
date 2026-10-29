import { Link } from 'react-router-dom';
import type { Incident } from '../../lib/api/types.ts';
import { IncidentStatusBadge, SeverityBadge } from '../../components/ui/Badges.tsx';
import { DateTime, RelativeTime } from '../../components/ui/Time.tsx';

/** A resource link that degrades to the snapshot name once the resource has been deleted. */
export function ResourceRef({ incident }: { incident: Incident }) {
  return incident.resource_id ? (
    <Link to={`/resources/${incident.resource_id}`}>{incident.resource_name}</Link>
  ) : (
    <span title="This resource has been deleted; the name is kept from when the incident opened.">
      {incident.resource_name} <span className="subtle">(deleted)</span>
    </span>
  );
}

/** Compact list for dashboards and resource pages. */
export function IncidentCompactList({ items }: { items: Incident[] }) {
  return (
    <ul className="stack" style={{ gap: 0 }}>
      {items.map((incident) => (
        <li
          key={incident.id}
          className="row"
          style={{
            justifyContent: 'space-between',
            padding: '10px 0',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div className="cell-title" style={{ flex: '1 1 240px' }}>
            <Link to={`/incidents/${incident.id}`}>{incident.title}</Link>
            <span className="cell-sub">
              <ResourceRef incident={incident} /> · triggered{' '}
              <RelativeTime iso={incident.triggered_at} />
            </span>
          </div>
          <div className="row">
            <SeverityBadge severity={incident.severity} />
            <IncidentStatusBadge status={incident.status} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function IncidentsTable({ items }: { items: Incident[] }) {
  return (
    <div className="table-wrap table-wrap--stack">
      <table className="table table--stack">
        <thead>
          <tr>
            <th scope="col">Incident</th>
            <th scope="col">Severity</th>
            <th scope="col">Status</th>
            <th scope="col">Resource</th>
            <th scope="col">Rule</th>
            <th scope="col">Triggered</th>
            <th scope="col">Acknowledged</th>
            <th scope="col">Resolved</th>
          </tr>
        </thead>
        <tbody>
          {items.map((incident) => (
            <tr key={incident.id}>
              <td data-label="Incident">
                <Link to={`/incidents/${incident.id}`} style={{ fontWeight: 600 }}>
                  {incident.title}
                </Link>
              </td>
              <td data-label="Severity">
                <SeverityBadge severity={incident.severity} />
              </td>
              <td data-label="Status">
                <IncidentStatusBadge status={incident.status} />
              </td>
              <td data-label="Resource">
                <ResourceRef incident={incident} />
              </td>
              <td data-label="Rule">{incident.rule_name}</td>
              <td className="nowrap" data-label="Triggered">
                <DateTime iso={incident.triggered_at} />
              </td>
              <td className="nowrap" data-label="Acknowledged">
                <DateTime iso={incident.acknowledged_at} empty="—" />
              </td>
              <td className="nowrap" data-label="Resolved">
                <DateTime iso={incident.resolved_at} empty="—" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

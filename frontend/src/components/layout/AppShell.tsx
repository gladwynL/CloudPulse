import { BellRing, LayoutDashboard, Menu, Server, ShieldAlert } from 'lucide-react';
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useOverview } from '../../features/overview/hooks.ts';
import { config } from '../../config.ts';
import { Button } from '../ui/Button.tsx';
import { ApiStatusPill } from './ApiStatusPill.tsx';
import { Logo } from './Logo.tsx';

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/resources', label: 'Resources', icon: Server, end: false },
  { to: '/alerts', label: 'Alert rules', icon: BellRing, end: false },
  { to: '/incidents', label: 'Incidents', icon: ShieldAlert, end: false },
] as const;

export function AppShell() {
  // The drawer is open for a specific path, so navigating closes it without an effect.
  const [openFor, setOpenFor] = useState<string | null>(null);
  const location = useLocation();
  const open = openFor === location.pathname;
  const setOpen = (value: boolean) => setOpenFor(value ? location.pathname : null);
  const overview = useOverview();
  const active = overview.data?.incidents.active ?? 0;

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpenFor(null);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar" id="primary-nav" data-open={open}>
        <NavLink to="/" className="brand" aria-label="CloudPulse home">
          <Logo />
          CloudPulse
        </NavLink>
        <nav aria-label="Primary">
          <ul className="nav">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <li key={to}>
                <NavLink to={to} end={end} className="nav__link">
                  <Icon size={18} aria-hidden="true" />
                  {label}
                  {to === '/incidents' && active > 0 && (
                    <span className="nav__badge" aria-label={`${active} active incidents`}>
                      {active}
                    </span>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="sidebar__footer">
          <p className="sidebar__note">
            Local development build. There is <strong>no authentication</strong>; do not expose this
            dashboard or its API to an untrusted network.
          </p>
          <span>
            Environment: <strong>{config.environment}</strong>
          </span>
        </div>
      </aside>
      <div className="scrim" data-open={open} onClick={() => setOpenFor(null)} aria-hidden="true" />

      <div className="shell-main">
        <header className="topbar">
          <Button
            className="topbar__menu"
            variant="ghost"
            iconOnly
            aria-label={open ? 'Close navigation' : 'Open navigation'}
            aria-expanded={open}
            aria-controls="primary-nav"
            onClick={() => setOpen(!open)}
          >
            <Menu size={20} aria-hidden="true" />
          </Button>
          <NavLink to="/" className="brand topbar__menu" aria-label="CloudPulse home">
            <Logo />
          </NavLink>
          <span className="topbar__spacer" />
          <ApiStatusPill />
        </header>
        <main className="main" id="main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

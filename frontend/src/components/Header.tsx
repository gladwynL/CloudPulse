import { config } from '../config.ts';
import { Logo } from './Logo.tsx';

export function Header() {
  return (
    <header className="header">
      <div className="container header__inner">
        <a className="brand" href="/" aria-label="CloudPulse home">
          <Logo />
          <span className="brand__name">CloudPulse</span>
        </a>
        <nav aria-label="Primary">
          <ul className="nav">
            <li>
              <a className="nav__link nav__link--active" href="/" aria-current="page">
                Overview
              </a>
            </li>
            <li>
              <a
                className="nav__link"
                href={`${config.apiUrl}/docs`}
                target="_blank"
                rel="noreferrer"
              >
                API Docs
              </a>
            </li>
          </ul>
        </nav>
      </div>
    </header>
  );
}

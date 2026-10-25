import { Header } from './components/Header.tsx';
import { Roadmap } from './components/Roadmap.tsx';
import { SystemStatusPanel } from './components/SystemStatusPanel.tsx';
import { config } from './config.ts';

export default function App() {
  return (
    <div className="app">
      <Header />
      <main className="container main">
        <section className="hero" aria-labelledby="hero-heading">
          <p className="hero__eyebrow">CloudPulse</p>
          <h1 id="hero-heading" className="hero__title">
            Cloud Monitoring &amp; Observability
          </h1>
          <p className="hero__lead">
            Health checks, uptime and latency tracking, alerting, and incident management for cloud
            services — built in deliberate, production-style phases.
          </p>
        </section>
        <div className="grid">
          <SystemStatusPanel />
          <Roadmap />
        </div>
      </main>
      <footer className="footer">
        <div className="container footer__inner">
          <span>CloudPulse v0.1.0</span>
          <span>
            Environment: <strong>{config.environment}</strong>
          </span>
        </div>
      </footer>
    </div>
  );
}

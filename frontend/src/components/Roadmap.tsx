const PHASES = [
  { id: 0, title: 'Foundation', summary: 'API, database, Docker, CI, and dashboard shell.' },
  {
    id: 1,
    title: 'Resource monitoring',
    summary: 'Register services; scheduled and manual HTTP checks; latest health via API.',
  },
  { id: 2, title: 'Historical metrics', summary: 'Per-check history, uptime, and latency trends.' },
  { id: 3, title: 'Alerts & incidents', summary: 'Threshold rules and the incident lifecycle.' },
  { id: 4, title: 'Monitoring dashboard', summary: 'Resource management and status views.' },
  { id: 5, title: 'Cloud deployment', summary: 'Azure deployment and infrastructure-as-code.' },
] as const;

const CURRENT_PHASE = 1;

export function Roadmap() {
  return (
    <section className="card" aria-labelledby="roadmap-heading">
      <div className="card__header">
        <div>
          <h2 id="roadmap-heading" className="card__title">
            Roadmap
          </h2>
          <p className="card__subtitle">Capabilities are delivered in phases.</p>
        </div>
      </div>
      <ol className="roadmap">
        {PHASES.map((phase) => {
          const current = phase.id === CURRENT_PHASE;
          return (
            <li
              key={phase.id}
              className={`roadmap__item${current ? ' roadmap__item--current' : ''}`}
              aria-current={current ? 'step' : undefined}
            >
              <span className="roadmap__phase">Phase {phase.id}</span>
              <span className="roadmap__title">
                {phase.title}
                {current && <span className="roadmap__tag">Current</span>}
              </span>
              <span className="roadmap__summary">{phase.summary}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

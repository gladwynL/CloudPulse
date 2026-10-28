import { PERIODS, type PeriodId } from '../../lib/utils/period.ts';

export function PeriodSelect({
  value,
  onChange,
}: {
  value: PeriodId;
  onChange: (id: PeriodId) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label="Time period">
      {PERIODS.map((period) => (
        <button
          key={period.id}
          type="button"
          aria-pressed={period.id === value}
          onClick={() => onChange(period.id)}
          title={period.label}
        >
          {period.id}
        </button>
      ))}
    </div>
  );
}

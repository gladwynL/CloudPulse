import { CircleAlert } from 'lucide-react';
import {
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';

interface Shell {
  label: string;
  hint?: ReactNode;
  error?: string;
  className?: string;
}

interface Aria {
  id: string;
  'aria-describedby'?: string;
  'aria-invalid'?: true;
}

/** Visible label, optional hint, and an error announced with the control it belongs to. */
function Field({
  label,
  hint,
  error,
  className,
  children,
}: Shell & { children: (a: Aria) => ReactNode }) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ');
  return (
    <div className={`field${className ? ` ${className}` : ''}`}>
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {children({
        id,
        'aria-describedby': describedBy || undefined,
        'aria-invalid': error ? true : undefined,
      })}
      {hint && (
        <p id={hintId} className="field__hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="field__error" role="alert">
          <CircleAlert size={14} aria-hidden="true" style={{ marginTop: 3, flex: 'none' }} />
          {error}
        </p>
      )}
    </div>
  );
}

export function TextField({
  label,
  hint,
  error,
  className,
  ...input
}: Shell & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <Field label={label} hint={hint} error={error} className={className}>
      {(aria) => <input className="input" {...aria} {...input} />}
    </Field>
  );
}

export function TextAreaField({
  label,
  hint,
  error,
  className,
  ...input
}: Shell & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <Field label={label} hint={hint} error={error} className={className}>
      {(aria) => <textarea className="textarea" {...aria} {...input} />}
    </Field>
  );
}

export function SelectField({
  label,
  hint,
  error,
  className,
  children,
  ...select
}: Shell & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <Field label={label} hint={hint} error={error} className={className}>
      {(aria) => (
        <select className="select" {...aria} {...select}>
          {children}
        </select>
      )}
    </Field>
  );
}

export function CheckboxField({
  label,
  hint,
  className,
  ...input
}: { label: string; hint?: ReactNode; className?: string } & Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'type'
>) {
  const id = useId();
  return (
    <div className={`field${className ? ` ${className}` : ''}`}>
      <label className="check" htmlFor={id}>
        <input id={id} type="checkbox" {...input} />
        <span>{label}</span>
      </label>
      {hint && <p className="field__hint">{hint}</p>}
    </div>
  );
}

/** A standalone select for toolbars, labelled for assistive technology but visually compact. */
export function FilterSelect({
  label,
  children,
  ...select
}: { label: string } & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      <select id={id} className="select" {...select}>
        {children}
      </select>
    </div>
  );
}

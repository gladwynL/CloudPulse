import { ArrowLeft } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useDocumentTitle } from '../../hooks/useDocumentTitle.ts';

export function PageHeader({
  title,
  subtitle,
  actions,
  back,
  documentTitle,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  back?: { to: string; label: string };
  /** Defaults to `title`; set when the visible heading is not a good tab title. */
  documentTitle?: string;
}) {
  useDocumentTitle(documentTitle ?? title);
  return (
    <header className="page__header">
      <div className="page__heading">
        {back && (
          <Link to={back.to} className="breadcrumb">
            <ArrowLeft size={14} aria-hidden="true" /> {back.label}
          </Link>
        )}
        <h1>{title}</h1>
        {subtitle && <div className="page__subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="page__actions">{actions}</div>}
    </header>
  );
}

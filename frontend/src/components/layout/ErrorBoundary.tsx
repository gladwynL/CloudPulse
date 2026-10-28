import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button } from '../ui/Button.tsx';

interface State {
  failed: boolean;
}

/** A render crash shows a recovery screen instead of a blank page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Message and component stack only: never request URLs or response bodies.
    console.error('UI render error:', error.message, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="state state--error" role="alert" style={{ margin: 24 }}>
        <p className="state__title">Something went wrong in the dashboard</p>
        <p className="state__body">
          The page hit an unexpected error. Your monitoring data is not affected.
        </p>
        <Button variant="primary" onClick={() => window.location.reload()}>
          Reload the page
        </Button>
      </div>
    );
  }
}

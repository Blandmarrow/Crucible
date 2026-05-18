import { Component, type ReactNode, type ErrorInfo } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return this.props.fallback ?? (
        <div style={{
          padding: "2rem", color: "var(--bad)", fontFamily: "var(--font-mono)",
          fontSize: "0.85rem", lineHeight: 1.6,
        }}>
          <strong>Something went wrong in this panel.</strong>
          <pre style={{ marginTop: "0.75rem", whiteSpace: "pre-wrap", opacity: 0.8 }}>
            {this.state.error.message}
          </pre>
          <button
            className="btn sm"
            style={{ marginTop: "1rem" }}
            onClick={() => this.setState({ error: null })}
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

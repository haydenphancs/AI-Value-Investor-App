// Information for LLM Agent
// This is a component that handles errors in the application.
// don't update this file!
import { Component, type CSSProperties, type ErrorInfo, type ReactNode } from "react";
import snError from "./sn-error.svg";
import snRefreshIcon from "./sn-refresh-icon.svg";

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  alignItems: "center",
  maxWidth: "50%",
  margin: "15% auto",
  backgroundColor: "white",
  color: "black",

  fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
};

const imageStyle: CSSProperties = {
  maxWidth: "440px",
  maxHeight: "280px",
  marginBottom: "12px",
};

const titleStyle: CSSProperties = {
  fontSize: "20px",
  lineHeight: "28px",
  fontWeight: 600,
  marginBottom: "4px",
};

const messageStyle: CSSProperties = {
  fontSize: "14px",
  lineHeight: "20px",
  color: "#66728A",
};

const buttonStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "4px",
  padding: "8px",
  fontSize: "14px",
  fontWeight: 500,
  color: "black",
  backgroundColor: "rgba(96, 97, 98, 0.06)",
  border: "none",
  borderRadius: "5px",
  cursor: "pointer",
  fontFamily: "inherit",
  lineHeight: "20px",
  minHeight: "36px",
  marginTop: "24px",
};

const buttonHoverStyle: CSSProperties = {
  backgroundColor: "rgba(96, 97, 98, 0.09)",
};

const refreshIconStyle: CSSProperties = {
  width: "16px",
  height: "16px",
};

const linkStyle: CSSProperties = {
  fontSize: "14px",
  color: "#66728A",
  textDecoration: "none",
  cursor: "pointer",
  marginTop: "8px",
  marginBottom: "24px",
  fontFamily: "inherit",
};

const linkHoverStyle: CSSProperties = {
  textDecoration: "underline",
};

const stackStyle: CSSProperties = {
  fontSize: "0.85rem",
  padding: "24px",
  borderRadius: "8px",
  backgroundColor: "#fff",
  border: "1px solid #fed7d7",
  overflowX: "auto",
  whiteSpace: "pre-wrap",
  color: "#BE0021",
  background: "rgba(255, 43, 83, 0.09)",
};

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  showErrorDetails: boolean;
}

export class SnErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = {
    hasError: false,
    error: null,
    showErrorDetails: false,
  };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      error: error,
      showErrorDetails: false,
    };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    if (typeof window === "undefined") {
      return;
    }

    if (window.parent && window.parent !== window) {
      const urlWithoutExtraPath = `${window.location.origin}/${window.location.pathname.split("/").at(1)}`;
      window.parent.postMessage(
        {
          source: "prototype",
          type: "APPLICATION_ERROR",
          payload: {
            message: error?.message ?? "Unknown error",
            stack: error?.stack,
            componentStack: errorInfo.componentStack,
            url: urlWithoutExtraPath,
          },
        },
        "*"
      );
    }
  }

  render(): ReactNode {
    if (this.state.hasError) {
      console.error(this.state.error);
      return (
        <div style={containerStyle}>
          <img src={snError} alt="Error illustration" style={imageStyle} />
          <div style={titleStyle}>Something went wrong</div>
          <p style={messageStyle}>Try refreshing or fixing with Supernova AI</p>
          <button
            style={buttonStyle}
            onClick={() => window.location.reload()}
            onMouseEnter={e => {
              Object.assign(e.currentTarget.style, buttonHoverStyle);
            }}
            onMouseLeave={e => {
              e.currentTarget.style.backgroundColor = buttonStyle.backgroundColor as string;
            }}
          >
            <img src={snRefreshIcon} alt="Refresh icon" style={refreshIconStyle} />
            Refresh Page
          </button>
          {this.state.error?.stack && (
            <>
              <a
                style={linkStyle}
                onClick={e => {
                  e.preventDefault();
                  this.setState({ showErrorDetails: !this.state.showErrorDetails });
                }}
                onMouseEnter={e => {
                  Object.assign(e.currentTarget.style, linkHoverStyle);
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.textDecoration = "none";
                }}
              >
                {this.state.showErrorDetails ? "Hide error" : "Show error"}
              </a>
              {this.state.showErrorDetails && <pre style={stackStyle}>{this.state.error.stack}</pre>}
            </>
          )}
        </div>
      );
    }

    return this.props.children;
  }
}

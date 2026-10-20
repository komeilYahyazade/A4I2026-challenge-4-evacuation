import { Component, StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { StoreProvider } from "./store";
import "./styles.css";

class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error)
      return (
        <div style={{ padding: 24 }}>
          <h2>Something broke in the console.</h2>
          <pre style={{ whiteSpace: "pre-wrap" }}>{this.state.error.message}</pre>
          <button onClick={() => location.reload()}>Reload</button>
        </div>
      );
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Boundary>
      <StoreProvider>
        <App />
      </StoreProvider>
    </Boundary>
  </StrictMode>,
);

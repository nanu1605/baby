import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { useBrain } from "./store";
import { subscribePulses } from "./graph/pulseBus";
import "./styles/tokens.css";
import "./styles/app.css";

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");

// Dev-only debugging handles (stripped from production builds).
if (import.meta.env.DEV) {
  const w = window as unknown as {
    __brain?: typeof useBrain;
    __subscribePulses?: typeof subscribePulses;
  };
  w.__brain = useBrain;
  w.__subscribePulses = subscribePulses;
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

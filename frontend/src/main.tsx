import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { DevicesProvider } from "./live/DevicesProvider";
import { LiveProvider } from "./live/LiveProvider";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LiveProvider>
      <DevicesProvider>
        <App />
      </DevicesProvider>
    </LiveProvider>
  </StrictMode>,
);

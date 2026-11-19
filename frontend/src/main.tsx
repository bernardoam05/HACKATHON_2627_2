import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { DevicesProvider } from "./live/DevicesProvider";
import { LiveProvider } from "./live/LiveProvider";
import { RoomsProvider } from "./live/RoomsProvider";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LiveProvider>
      <DevicesProvider>
        <RoomsProvider>
          <App />
        </RoomsProvider>
      </DevicesProvider>
    </LiveProvider>
  </StrictMode>,
);

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { readTheme, writeTheme } from "./lib";
import "./styles.css";

writeTheme(readTheme());
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

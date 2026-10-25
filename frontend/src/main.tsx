import "./index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { api } from "./api/client";
import { ApiProvider } from "./api/context";
import App from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <ApiProvider client={api}>
      <App />
    </ApiProvider>
  </StrictMode>,
);

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@/index.css";
import App from "@/App";

// Suppress "Object is disposed" errors from LightweightCharts' internal RAF/ResizeObserver
// that can fire once after chart.remove() is called during component unmount (e.g. logout).
// This is a known library issue and does not affect functionality.
window.addEventListener('error', (event) => {
  if (event.error?.message?.includes('Object is disposed')) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}, true);

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);

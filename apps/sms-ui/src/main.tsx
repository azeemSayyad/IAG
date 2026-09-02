import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import "./index.css";
import "./portal-shell.css";
import { ensureAuth } from "./lib/auth";
import { installThemeBridge } from "./lib/theme";
import { loadI18nDict, translatePage } from "./lib/i18n";
import I18N_DICT from "./i18n-dict";

// Honor the same theme / dark-mode / sidebar / font prefs the user picked in the
// portal (stored in localStorage by the portal). Recolors the SMS app to match.
installThemeBridge();

// Load i18n dictionary (will be applied by useI18n hook after React renders)
try {
  loadI18nDict(I18N_DICT);
} catch (_) {}

// Reuse the portal's existing login. If no token, bounce to /login.html.
ensureAuth();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </React.StrictMode>,
);

/* theme.ts — Theme bridge for the SMS app.
 *
 * The static portal (apps/frontendall) themes every page via a <head> init
 * script in dashboard.html + prefs-extras.js. Those scripts read the user's
 * preferences from localStorage and apply them to the document root:
 *
 *   ebTheme    brand | forest | indigo | rose | slate | amber   → --accent / --accent-2 / --accent-hover
 *   ebMode     light | dark | auto                              → html[data-mode]
 *   ebLayout   spacious | compact                               → html[data-layout]
 *   ebSidebar  labels | icons                                   → html[data-sidebar]
 *   ebFontSize sm | md | lg | xl                                → html { font-size }
 *
 * The SMS app (this Vite/React build) lives at /sms/ inside the same portal and
 * shares the same localStorage origin, but previously ignored these keys — so
 * the chosen color theme + dark mode + collapsed sidebar never carried over.
 *
 * The palette itself lives in ONE place — apps/frontendall/brand.js, loaded at
 * the site root by index.html — so a rebrand never has to touch this file. This
 * module only forwards the user's stored preferences to it, and keeps them in
 * sync with `storage` events (theme changed in another tab/the portal) and with
 * system dark-mode changes when ebMode === 'auto'.
 */

/** window.EB_BRAND, published by /brand.js. */
type Brand = {
  DEFAULT: string;
  normalize: (v: string | null) => string;
  apply: (v: string | null) => void;
  applyFont: (v: string | null) => void;
  theme: () => { a: string; a2: string; ah: string };
  css: (name: string) => string;
};
function brand(): Brand | undefined {
  return (window as unknown as { EB_BRAND?: Brand }).EB_BRAND;
}

/** A brand colour as a real value — for SVG attributes and <canvas>, neither
 *  of which resolves var(). Falls back to the computed custom property. */
export function brandColor(name: string): string {
  return brand()?.css(name) ?? "";
}

function root() {
  return document.documentElement;
}

export function applyTheme(v: string | null) {
  brand()?.apply(v);
}

export function applyFont(v: string | null) {
  brand()?.applyFont(v);
}

export function applyMode(v: string | null) {
  let m = v || "light";
  if (m === "auto") {
    m = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  root().setAttribute("data-mode", m);
}

export function applyLayout(v: string | null) {
  root().setAttribute("data-layout", v || "spacious");
}

export function applySidebar(v: string | null) {
  root().setAttribute("data-sidebar", v || "labels");
}

/** Apply every preference from the current localStorage values. */
export function applyAllPrefs() {
  try {
    applyTheme(localStorage.getItem("ebTheme"));
    applyFont(localStorage.getItem("ebFontSize"));
    applyMode(localStorage.getItem("ebMode"));
    applyLayout(localStorage.getItem("ebLayout"));
    applySidebar(localStorage.getItem("ebSidebar"));
  } catch {
    // best-effort — preferences shouldn't break the app
  }
}

/**
 * Install the theme bridge: apply current prefs, then keep them in sync with
 * changes made elsewhere (the portal Settings page writes the same keys) via
 * the `storage` event, and follow the system color scheme while in auto mode.
 *
 * Also exposes the portal's window.__ebApply* globals so any shared script that
 * expects them (and the SMS sidebar collapse toggle) can drive the same code.
 */
export function installThemeBridge() {
  applyAllPrefs();

  // Expose the same globals the portal defines, so calling them recolors/relays
  // the SMS app identically. (__ebApplyTheme / __ebApplyFont come from brand.js.)
  (window as unknown as Record<string, unknown>).__ebApplyMode = applyMode;
  (window as unknown as Record<string, unknown>).__ebApplyLayout = applyLayout;
  (window as unknown as Record<string, unknown>).__ebApplySidebar = applySidebar;

  // Theme changed in another tab / the portal Settings page.
  window.addEventListener("storage", (e) => {
    if (!e.key) {
      applyAllPrefs();
      return;
    }
    switch (e.key) {
      case "ebTheme": applyTheme(e.newValue); break;
      case "ebFontSize": applyFont(e.newValue); break;
      case "ebMode": applyMode(e.newValue); break;
      case "ebLayout": applyLayout(e.newValue); break;
      case "ebSidebar": applySidebar(e.newValue); break;
    }
  });

  // Follow system dark mode while in auto.
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if ((localStorage.getItem("ebMode") || "light") === "auto") applyMode("auto");
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
}

/** Read the current sidebar pref (labels|icons). */
export function getSidebarPref(): "labels" | "icons" {
  try {
    return localStorage.getItem("ebSidebar") === "icons" ? "icons" : "labels";
  } catch {
    return "labels";
  }
}

/** Toggle the sidebar pref, persist it, apply it, and return the new value. */
export function toggleSidebarPref(): "labels" | "icons" {
  const next = getSidebarPref() === "icons" ? "labels" : "icons";
  try {
    localStorage.setItem("ebSidebar", next);
  } catch {
    // ignore
  }
  applySidebar(next);
  return next;
}

/* theme.ts — Theme bridge for the SMS app.
 *
 * The static portal (apps/frontendall) themes every page via a <head> init
 * script in dashboard.html + prefs-extras.js. Those scripts read the user's
 * preferences from localStorage and apply them to the document root:
 *
 *   ebTheme    warm | forest | indigo | rose | slate | amber   → --accent / --accent-2 / --accent-hover
 *   ebMode     light | dark | auto                              → html[data-mode]
 *   ebLayout   spacious | compact                               → html[data-layout]
 *   ebSidebar  labels | icons                                   → html[data-sidebar]
 *   ebFontSize sm | md | lg | xl                                → html { font-size }
 *
 * The SMS app (this Vite/React build) lives at /sms/ inside the same portal and
 * shares the same localStorage origin, but previously ignored these keys — so
 * the chosen color theme + dark mode + collapsed sidebar never carried over.
 *
 * This module mirrors the EXACT contract (TM accent map + FM font map copied
 * verbatim from dashboard.html's head script) and applies it to <html> on
 * startup. It also listens for `storage` events (theme changed in another
 * tab/the portal) and for system dark-mode changes when ebMode === 'auto'.
 */

// Accent map — copied verbatim from dashboard.html's TM (a/a2/ah per theme).
const TM: Record<string, { a: string; a2: string; ah: string; g: string; gl: string }> = {
  warm:   { a: "#C97B3A", a2: "#E0995E", ah: "#B26A2D", g: "linear-gradient(135deg,#FFFBF5 0%,#FFEFDC 50%,#FFF6E9 100%)", gl: "rgba(178,106,45,.08)" },
  forest: { a: "#4F8268", a2: "#6B9F86", ah: "#3D6651", g: "linear-gradient(135deg,#F5FBF7 0%,#E0F1E6 50%,#E8F4ED 100%)", gl: "rgba(63,109,84,.08)" },
  indigo: { a: "#5E7BA8", a2: "#7B96BD", ah: "#4A6488", g: "linear-gradient(135deg,#F5F7FB 0%,#DCE4F1 50%,#E5EBF4 100%)", gl: "rgba(74,100,136,.08)" },
  rose:   { a: "#A3525C", a2: "#B97077", ah: "#82414B", g: "linear-gradient(135deg,#FBF5F6 0%,#F1DCDF 50%,#F4E5E8 100%)", gl: "rgba(130,65,75,.08)" },
  slate:  { a: "#2D3340", a2: "#5A6275", ah: "#1A1F2A", g: "linear-gradient(135deg,#B8BCC5 0%,#9DA3AD 50%,#ABB0BA 100%)", gl: "rgba(26,31,42,.14)" },
  amber:  { a: "#9C7842", a2: "#B89058", ah: "#7C5E2F", g: "linear-gradient(135deg,#FBF7F0 0%,#F0E5D0 50%,#F4EBDB 100%)", gl: "rgba(124,94,47,.08)" },
};

// Font-size map — copied verbatim from dashboard.html's FM.
const FM: Record<string, string> = { sm: "14px", md: "16px", lg: "18px", xl: "20px" };

function root() {
  return document.documentElement;
}

/** "#RRGGBB" -> "r, g, b" so rgba(var(--accent-rgb), a) tints follow the theme. */
function hexToRgb(hex: string): string {
  let h = (hex || "").trim().replace("#", "");
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  if (h.length !== 6) return "";
  const n = parseInt(h, 16);
  if (Number.isNaN(n)) return "";
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
}

export function applyTheme(v: string | null) {
  const t = TM[v || "warm"] || TM.warm;
  const D = root();
  D.style.setProperty("--accent", t.a);
  D.style.setProperty("--accent-2", t.a2);
  D.style.setProperty("--accent-hover", t.ah);
  D.style.setProperty("--accent-rgb", hexToRgb(t.a));
  // Mirror the portal's injected #ebPrefsTheme style block: recolor the page
  // background gradients and the brand logo gradient to the chosen theme so the
  // SMS pages match the portal's canvas (the portal sets these via body::before
  // / body::after + .sb-brand-dot).
  let st = document.getElementById("ebPrefsTheme") as HTMLStyleElement | null;
  if (!st) {
    st = document.createElement("style");
    st.id = "ebPrefsTheme";
    document.head.appendChild(st);
  }
  st.textContent =
    "body::before{background:" + t.g + " !important}" +
    "body::after{background:radial-gradient(ellipse 95% 85% at 50% 50%,transparent 55%," + t.gl + " 100%) !important}" +
    ".sb-brand-dot{background:linear-gradient(135deg," + t.a + "," + t.a2 + ") !important}";
}

export function applyFont(v: string | null) {
  root().style.fontSize = FM[v || "md"] || "16px";
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
  // the SMS app identically.
  (window as unknown as Record<string, unknown>).__ebApplyTheme = applyTheme;
  (window as unknown as Record<string, unknown>).__ebApplyFont = applyFont;
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

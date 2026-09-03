/*
 * Mobile hard gate — CURRENTLY DISABLED (see the early `return;` in the IIFE below);
 * the web app is allowed on phones/tablets. The original behaviour is documented here
 * for when the native apps ship and the gate is re-enabled.
 *
 * On a phone/tablet the entire web UI is replaced by a compulsory "install the
 * app" screen (App Store / Google Play) — there is no dismiss; the web app is
 * desktop-only and mobile users must use the native app. Loaded on every portal
 * page (via prefs-extras) + the React /sms app + the few pages that load it
 * directly (login / index / sales-dashboard / dispositions). Self-contained
 * (no framework / __ebAPI dependency) and rendered INLINE — no external file to
 * fetch, so it cannot 404 on the live host.
 *
 * The markup/CSS mirrors /mobile-gate.html (the hand-built design). Every class
 * is namespaced under #appHardGate so no host-page CSS can leak into it.
 *
 * Mobile detection uses pointer + hover, NOT screen size: a real phone/tablet
 * has a coarse, non-hovering primary pointer; a touchscreen LAPTOP has a
 * mouse/trackpad (fine pointer, can hover) so it is treated as desktop even at
 * high display scaling. This is what keeps touchscreen laptops out of the gate.
 *
 * Hidden bypass for team/web testing only: open any URL with ?forceweb=1 once
 * (persists), or ?forceweb=0 to re-enable. Real users never see this.
 *
 * Touches NO backend / SMS send path — pure client-side gating.
 */
;(function () {
  "use strict";

  // ── MOBILE HARD GATE DISABLED ────────────────────────────────────────────
  // The web app is now allowed on phones/tablets — the compulsory "install the
  // app" screen no longer blocks mobile. This file is kept as a no-op so every
  // page's `/app-gate.js` reference stays valid (no 404). To RE-ENABLE the gate
  // (e.g. once the native apps are published in the stores), delete the line
  // below. Touches NO backend / SMS send path.
  return;
  // ─────────────────────────────────────────────────────────────────────────

  // ── CONFIG — set these to the real store listings ────────────────────────
  var APP_STORE_URL  = "#";   // TODO: https://apps.apple.com/app/id0000000000
  var PLAY_STORE_URL = "#";   // TODO: https://play.google.com/store/apps/details?id=your.app
  // ─────────────────────────────────────────────────────────────────────────

  try {
    var q = location.search + location.hash;
    if (/[?&]forceweb=1/.test(q)) localStorage.setItem("forceWeb", "1");
    if (/[?&]forceweb=0/.test(q)) localStorage.removeItem("forceWeb");
    if (localStorage.getItem("forceWeb") === "1") return;
  } catch (e) { /* ignore */ }

  function isMobile() {
    var ua = (navigator.userAgent || "").toLowerCase();
    if (/android|iphone|ipod|ipad|iemobile|blackberry|opera mini|windows phone|mobile/.test(ua)) return true;
    // Real phones/tablets: coarse + non-hovering PRIMARY pointer. Touchscreen
    // laptops have a mouse/trackpad (fine pointer, hover-capable) → NOT gated,
    // regardless of screen dimensions or display scaling.
    try {
      var coarse  = window.matchMedia && window.matchMedia("(pointer:coarse)").matches;
      var noHover = window.matchMedia && window.matchMedia("(hover:none)").matches;
      if (coarse && noHover && (navigator.maxTouchPoints || 0) > 0) return true;
    } catch (e) { /* ignore */ }
    return false;
  }
  if (!isMobile()) return;

  // Accent comes from brand.js — the portal's single source of truth for colour.
  // This only derives the three alpha shades the gate's own chrome needs.
  function accent() {
    var b = window.EB_BRAND;
    var css = function (n) {
      return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    };
    var a  = b ? b.theme().a  : css('--accent');
    var a2 = b ? b.theme().a2 : css('--accent-2');
    var rgb = css('--accent-rgb');
    return {
      a: a, a2: a2,
      glow: 'rgba(' + rgb + ',0.40)',
      soft: 'rgba(' + rgb + ',0.14)',
      line: 'rgba(' + rgb + ',0.38)'
    };
  }

  function build() {
    if (document.getElementById("appHardGate")) return;

    var dark = false;
    try { dark = localStorage.getItem("ebMode") === "dark"; } catch (e) {}
    var t = accent();

    // The announcement gate is irrelevant on mobile — hide it behind this one.
    var hideAnn = document.createElement("style");
    hideAnn.textContent = "#annOverlay{display:none!important}";
    (document.head || document.documentElement).appendChild(hideAnn);
    try { document.documentElement.style.overflow = "hidden"; } catch (e) {}

    // All selectors scoped under #appHardGate → no host-page CSS can leak in.
    var st = document.createElement("style");
    st.id = "appHardGateStyle";
    st.textContent =
      "#appHardGate{position:fixed;inset:0;z-index:2147483647;overflow:auto;-webkit-overflow-scrolling:touch;" +
        "display:flex;align-items:center;justify-content:center;padding:40px 26px;" +
        "font-family:Inter,system-ui,-apple-system,sans-serif;text-align:center;color:#2D3340;background:var(--a97)}" +
      "#appHardGate *{box-sizing:border-box;margin:0;padding:0}" +
      "#appHardGate::before{content:'';position:absolute;inset:-15%;z-index:0;" +
        "background:linear-gradient(135deg,var(--a98) 0%,var(--a93) 50%,var(--a96) 100%)}" +
      "#appHardGate::after{content:'';position:absolute;inset:-10%;z-index:0;pointer-events:none;" +
        "background:radial-gradient(ellipse 95% 85% at 50% 50%,transparent 55%," + t.soft + " 100%)}" +
      "#appHardGate.eb-dark{background:#0d1016}" +
      "#appHardGate.eb-dark::before{background:radial-gradient(120% 80% at 50% 28%,#1b2433 0%,#11151d 60%,#0d1016 100%)}" +
      "#appHardGate.eb-dark::after{background:radial-gradient(ellipse 95% 85% at 50% 50%,transparent 55%,rgba(0,0,0,0.35) 100%)}" +
      // Card
      "#appHardGate .ahg-card{position:relative;z-index:1;width:100%;max-width:440px;" +
        "background:rgba(255,255,255,0.60);backdrop-filter:blur(24px) saturate(160%);-webkit-backdrop-filter:blur(24px) saturate(160%);" +
        "border:1px solid rgba(26,31,42,0.09);border-radius:24px;padding:40px 40px 22px;" +
        "box-shadow:0 1px 2px rgba(26,31,42,0.04),0 28px 72px " + t.soft + ",0 10px 24px rgba(26,31,42,0.09),inset 0 1px 0 rgba(255,255,255,0.60)}" +
      "#appHardGate.eb-dark .ahg-card{background:rgba(30,34,44,0.72);border-color:rgba(255,255,255,0.09);" +
        "box-shadow:0 1px 2px rgba(0,0,0,.30),0 28px 72px rgba(0,0,0,.40),0 10px 24px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,0.06)}" +
      "#appHardGate .ahg-card::before{content:'';position:absolute;top:0;left:10%;right:10%;height:1px;" +
        "background:linear-gradient(90deg,transparent," + t.line + ",transparent);pointer-events:none}" +
      // Logo
      "#appHardGate .ahg-logo{width:80px;height:80px;border-radius:22px;margin:0 auto 28px;" +
        "background:linear-gradient(135deg," + t.a + "," + t.a2 + ");border:1px solid rgba(255,255,255,0.22);" +
        "display:flex;align-items:center;justify-content:center;" +
        "box-shadow:0 6px 24px " + t.glow + ",0 2px 6px " + t.soft + ",inset 0 1px 0 rgba(255,255,255,0.30)}" +
      "#appHardGate .ahg-logo svg{width:38px;height:38px}" +
      // Heading + body
      "#appHardGate .ahg-h1{font-size:1.875rem;font-weight:500;letter-spacing:-0.02em;line-height:1.2;color:#1A1F2A;margin-bottom:20px}" +
      "#appHardGate.eb-dark .ahg-h1{color:var(--n91)}" +
      "#appHardGate .ahg-body{font-size:0.9375rem;line-height:1.6;color:rgba(26,31,42,0.58);margin:0 auto 36px;max-width:320px}" +
      "#appHardGate.eb-dark .ahg-body{color:rgba(240,234,224,0.58)}" +
      // Store buttons
      "#appHardGate .ahg-actions{display:flex;flex-direction:column;gap:12px}" +
      "#appHardGate .ahg-btn{display:flex;align-items:center;gap:14px;padding:14px 20px;background:rgba(255,255,255,0.80);" +
        "border:1px solid rgba(26,31,42,0.18);border-radius:14px;text-decoration:none;text-align:left;" +
        "box-shadow:0 1px 3px rgba(26,31,42,0.06),inset 0 1px 0 rgba(255,255,255,0.70);" +
        "transition:background 160ms,border-color 160ms,box-shadow 160ms,transform 120ms}" +
      "#appHardGate .ahg-btn:active{transform:translateY(1px)}" +
      "#appHardGate.eb-dark .ahg-btn{background:rgba(255,255,255,0.06);border-color:rgba(255,255,255,0.10);" +
        "box-shadow:0 1px 3px rgba(0,0,0,0.20),inset 0 1px 0 rgba(255,255,255,0.06)}" +
      "#appHardGate .ahg-btn .ahg-ico{width:30px;height:30px;flex-shrink:0;display:flex;align-items:center;justify-content:center}" +
      "#appHardGate .ahg-btn .ahg-lbl{flex:1;min-width:0}" +
      "#appHardGate .ahg-btn .ahg-lbl .ahg-sub{display:block;font-size:0.6875rem;font-weight:500;color:rgba(26,31,42,0.45);line-height:1;margin-bottom:3px}" +
      "#appHardGate .ahg-btn .ahg-lbl .ahg-main{display:block;font-size:1rem;font-weight:700;letter-spacing:-0.01em;color:#1A1F2A;line-height:1.1}" +
      "#appHardGate.eb-dark .ahg-btn .ahg-lbl .ahg-sub{color:rgba(240,234,224,0.45)}" +
      "#appHardGate.eb-dark .ahg-btn .ahg-lbl .ahg-main{color:var(--n91)}" +
      "#appHardGate.eb-dark .ahg-btn .apple-fill{fill:var(--n91)}" +
      // Footer — inside the card, sitting near the bottom (below the buttons).
      "#appHardGate .ahg-foot{margin-top:24px;text-align:center;" +
        "font-size:0.8125rem;color:rgba(26,31,42,0.50);letter-spacing:0.01em}" +
      "#appHardGate.eb-dark .ahg-foot{color:rgba(240,234,224,0.45)}";
    (document.head || document.documentElement).appendChild(st);

    var g = document.createElement("div");
    g.id = "appHardGate";
    if (dark) g.className = "eb-dark";
    g.innerHTML =
      '<div class="ahg-card">' +
        '<div class="ahg-logo">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/>' +
            '<path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/>' +
          '</svg>' +
        '</div>' +
        '<h1 class="ahg-h1">This part lives<br>in the app</h1>' +
        '<p class="ahg-body">Your dashboard, messages, and uploads run on the app. Install it to pick up right where you left off.</p>' +
        '<div class="ahg-actions">' +
          '<a href="' + PLAY_STORE_URL + '" class="ahg-btn">' +
            '<span class="ahg-ico"><svg viewBox="0 0 24 24" width="26" height="26"><path d="M3.6 2.3c-.3.2-.5.6-.5 1.1v17.2c0 .5.2.9.5 1.1l.1.1L13 12.1v-.2L3.7 2.2l-.1.1z" fill="#34a853"/><path d="M16.3 15.2l-3.3-3.1v-.2l3.3-3.1.1.1 3.9 2.2c1.1.6 1.1 1.6 0 2.3l-3.9 2.2-.1-.4z" fill="#fbbc04"/><path d="M16.4 15.1L13 11.9l-9.4 9.6c.4.4 1 .4 1.7 0l11.1-6.4" fill="#ea4335"/><path d="M16.4 8.9L5.3 2.5c-.7-.4-1.3-.4-1.7 0L13 12.1l3.4-3.2z" fill="#4285f4"/></svg></span>' +
            '<span class="ahg-lbl"><span class="ahg-sub">Get it on</span><span class="ahg-main">Google Play</span></span>' +
          '</a>' +
          '<a href="' + APP_STORE_URL + '" class="ahg-btn">' +
            '<span class="ahg-ico"><svg viewBox="0 0 24 24" width="26" height="26"><path class="apple-fill" fill="#1A1F2A" d="M16.5 1.7c.1 1-.3 2-1 2.8-.7.8-1.7 1.4-2.7 1.3-.1-1 .4-2 1-2.7.7-.8 1.8-1.4 2.7-1.4zM19 17.3c-.5 1.1-.7 1.6-1.3 2.6-.9 1.4-2.1 3.1-3.6 3.1-1.3 0-1.7-.9-3.5-.8-1.8 0-2.2.8-3.5.8-1.5 0-2.7-1.6-3.6-3-2.5-3.9-2.7-8.4-1.2-10.8 1-1.7 2.7-2.7 4.3-2.7 1.6 0 2.6.9 3.9.9 1.3 0 2-.9 3.9-.9 1.4 0 2.9.8 3.9 2.1-3.4 1.9-2.9 6.8.3 8.7z"/></svg></span>' +
            '<span class="ahg-lbl"><span class="ahg-sub">Download on the</span><span class="ahg-main">App Store</span></span>' +
          '</a>' +
        '</div>' +
        '<div class="ahg-foot">Free · takes about 20 seconds to install</div>' +
      '</div>';
    document.body.appendChild(g);
  }

  if (document.body) build();
  else document.addEventListener("DOMContentLoaded", build);
})();

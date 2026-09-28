/*
 * Announcement popup (loaded on every portal page + the /sms app).
 *
 * While the signed-in user has an announcement they have not acknowledged, the
 * whole screen is blurred and blocked by a popup; one "Received" button acks it
 * (and shows the next, if any). The server decides who an announcement is for —
 * GET /announcements/pending — so this file only asks and renders.
 *
 * It appears within a second of being sent: the backend nudges every recipient
 * with the socket event `inapp_message` {kind:"announcement"} (portal pages get
 * it as `launchpad:realtime:inapp_message` from services/api.js; the SPA's
 * PortalShell re-dispatches the same window event). A 30s poll + a check on
 * focus cover anyone whose socket is down.
 *
 * Self-contained (no __ebAPI / framework), so one file serves both apps. Colours
 * come from brand.js (--accent / --accent-2 / --accent-rgb); dark mode follows
 * html[data-mode="dark"], which both apps set.
 */
;(function () {
  "use strict";
  if (window.__ebAnnouncementsLoaded) return;
  window.__ebAnnouncementsLoaded = true;

  var isLocal = location.hostname === "localhost" || location.hostname === "127.0.0.1";
  var BASE = (isLocal && location.port === "13000")
    ? location.protocol + "//" + location.hostname + ":18000"
    : location.origin;
  var API = BASE + "/api/v1";

  function token() { try { return localStorage.getItem("access_token"); } catch (e) { return null; } }
  function authHeaders(extra) {
    var h = extra || {};
    var t = token(); if (t) h["Authorization"] = "Bearer " + t;
    return h;
  }
  function get(path) {
    return fetch(API + path, { headers: authHeaders({}), cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }
  function ackPost(id) {
    return fetch(API + "/announcements/" + encodeURIComponent(id) + "/ack",
      { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: "{}" })
      .then(function (r) { return r.ok; })
      .catch(function () { return false; });
  }

  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function ago(iso) {
    var t = Date.parse(iso || ""); if (!t) return "";
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return new Date(t).toLocaleDateString([], { month: "short", day: "numeric" });
  }

  var CSS =
    "#annOverlay{position:fixed;inset:0;z-index:2147483647;display:flex;align-items:center;justify-content:center;padding:16px;" +
      "background:rgba(15,23,42,.50);backdrop-filter:blur(10px) saturate(120%);-webkit-backdrop-filter:blur(10px) saturate(120%);" +
      "animation:annFade .22s ease-out;font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif}" +
    "#annOverlay .ann-card{position:relative;width:min(520px,100%);max-height:calc(100vh - 32px);display:flex;flex-direction:column;" +
      "background:#fff;border-radius:20px;overflow:hidden;border:1px solid rgba(15,23,42,.06);" +
      "box-shadow:0 30px 80px rgba(15,23,42,.35),0 2px 6px rgba(15,23,42,.08);animation:annPop .32s cubic-bezier(.2,.9,.3,1.2)}" +
    "#annOverlay .ann-top{position:relative;padding:24px 26px 18px;display:flex;align-items:center;gap:14px;" +
      "background:linear-gradient(135deg,rgba(var(--accent-rgb,37,99,235),.14),rgba(var(--accent-rgb,37,99,235),.02) 70%)}" +
    "#annOverlay .ann-top::after{content:'';position:absolute;left:0;right:0;top:0;height:4px;" +
      "background:linear-gradient(90deg,var(--accent,#1E3A8A),var(--accent-2,#38BDF8))}" +
    "#annOverlay .ann-ic{flex:none;width:48px;height:48px;border-radius:14px;display:flex;align-items:center;justify-content:center;color:#fff;" +
      "background:linear-gradient(135deg,var(--accent,#1E3A8A),var(--accent-2,#38BDF8));box-shadow:0 8px 20px rgba(var(--accent-rgb,37,99,235),.35)}" +
    "#annOverlay .ann-ic svg{width:24px;height:24px}" +
    "#annOverlay .ann-kicker{font-size:.7rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--accent,#1E3A8A)}" +
    "#annOverlay .ann-meta{margin-top:3px;font-size:.8125rem;color:#64748B}" +
    "#annOverlay .ann-meta b{color:#0F172A;font-weight:700}" +
    "#annOverlay .ann-count{margin-left:auto;align-self:flex-start;padding:4px 10px;border-radius:999px;font-size:.7rem;font-weight:800;" +
      "color:var(--accent,#1E3A8A);background:rgba(var(--accent-rgb,37,99,235),.12);white-space:nowrap}" +
    "#annOverlay .ann-body{padding:20px 26px 24px;overflow:auto;font-size:1.0625rem;line-height:1.65;color:#0F172A;" +
      "white-space:pre-wrap;word-wrap:break-word;font-weight:500}" +
    "#annOverlay .ann-foot{padding:0 26px 24px}" +
    "#annOverlay .ann-btn{width:100%;height:52px;border:none;border-radius:14px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:9px;" +
      "font:700 1rem Inter,system-ui,sans-serif;letter-spacing:.01em;color:#fff;" +
      "background:linear-gradient(135deg,var(--accent,#1E3A8A),var(--accent-2,#38BDF8));" +
      "box-shadow:0 10px 24px rgba(var(--accent-rgb,37,99,235),.32);transition:transform .15s ease,box-shadow .15s ease,opacity .25s ease}" +
    "#annOverlay .ann-btn:hover{transform:translateY(-1px);box-shadow:0 14px 30px rgba(var(--accent-rgb,37,99,235),.40)}" +
    "#annOverlay .ann-btn:active{transform:translateY(0)}" +
    "#annOverlay .ann-btn:focus-visible{outline:3px solid rgba(var(--accent-rgb,37,99,235),.45);outline-offset:3px}" +
    "#annOverlay .ann-btn[disabled]{opacity:.55;cursor:default;transform:none}" +
    "#annOverlay .ann-btn svg{width:20px;height:20px}" +
    "html[data-mode='dark'] #annOverlay{background:rgba(3,6,12,.62)}" +
    "html[data-mode='dark'] #annOverlay .ann-card{background:#161A22;border-color:rgba(255,255,255,.08);box-shadow:0 30px 80px rgba(0,0,0,.6)}" +
    "html[data-mode='dark'] #annOverlay .ann-top{background:linear-gradient(135deg,rgba(var(--accent-rgb,37,99,235),.22),rgba(255,255,255,0) 70%)}" +
    "html[data-mode='dark'] #annOverlay .ann-kicker,html[data-mode='dark'] #annOverlay .ann-count{color:#9CC3FF}" +
    "html[data-mode='dark'] #annOverlay .ann-count{background:rgba(255,255,255,.08)}" +
    "html[data-mode='dark'] #annOverlay .ann-meta{color:#8B95A5}" +
    "html[data-mode='dark'] #annOverlay .ann-meta b,html[data-mode='dark'] #annOverlay .ann-body{color:#EEF1F5}" +
    "@keyframes annFade{from{opacity:0}to{opacity:1}}" +
    "@keyframes annPop{from{opacity:0;transform:translateY(10px) scale(.96)}to{opacity:1;transform:none}}" +
    "@media (prefers-reduced-motion:reduce){#annOverlay,#annOverlay .ann-card{animation:none}}";

  var ICON_MEGAPHONE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 11 18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/></svg>';
  var ICON_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';

  function ensureStyle() {
    if (document.getElementById("annStyle")) return;
    var st = document.createElement("style");
    st.id = "annStyle"; st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
  }

  var showing = false;

  function render(ann, total) {
    if (document.getElementById("annOverlay")) return;
    ensureStyle();
    showing = true;
    var ov = document.createElement("div");
    ov.id = "annOverlay";
    var from = ann.sender_name ? "From <b>" + esc(ann.sender_name) + "</b>" : "From your admin";
    var when = ago(ann.created_at);
    ov.innerHTML =
      '<div class="ann-card" role="alertdialog" aria-modal="true" aria-labelledby="annKicker" aria-describedby="annText" tabindex="-1">' +
        '<div class="ann-top">' +
          '<div class="ann-ic">' + ICON_MEGAPHONE + '</div>' +
          '<div><div class="ann-kicker" id="annKicker">Announcement</div>' +
            '<div class="ann-meta">' + from + (when ? " · " + esc(when) : "") + '</div></div>' +
          (total > 1 ? '<span class="ann-count">1 of ' + total + '</span>' : '') +
        '</div>' +
        '<div class="ann-body" id="annText">' + esc(ann.body) + '</div>' +
        '<div class="ann-foot"><button id="annReceived" class="ann-btn" type="button" disabled>' +
          ICON_CHECK + '<span>Received</span></button></div>' +
      '</div>';
    document.body.appendChild(ov);
    var card = ov.querySelector(".ann-card"), btn = ov.querySelector("#annReceived");
    try { card.focus({ preventScroll: true }); } catch (e) {}
    // A short guard so an Enter/click already in flight (typing, a double-click
    // on the page underneath) can't dismiss it before it has even been seen.
    setTimeout(function () { btn.disabled = false; }, 700);
    btn.addEventListener("click", function () {
      if (btn.disabled) return;
      btn.disabled = true; btn.lastChild.textContent = "Saving…";
      ackPost(ann.id).then(function (ok) {
        if (!ok) { btn.disabled = false; btn.lastChild.textContent = "Received — try again"; return; }
        var el = document.getElementById("annOverlay"); if (el) el.remove();
        showing = false;
        check();   // show the next one, if any
      });
    });
  }

  // Keep keyboard focus inside the popup while it is up.
  document.addEventListener("keydown", function (e) {
    if (!showing || e.key !== "Tab") return;
    var btn = document.getElementById("annReceived");
    if (btn) { e.preventDefault(); btn.focus(); }
  }, true);

  var inflight = false;
  function check() {
    if (showing || inflight || !token()) return;
    inflight = true;
    get("/announcements/pending").then(function (r) {
      inflight = false;
      var list = (r && r.pending) || [];
      if (list.length) render(list[0], list.length);
    });
  }

  function start() {
    if (!token()) return;          // not signed in -> nothing to show
    // Portal pages: make sure the socket is open (idempotent; absent in the SPA,
    // whose PortalShell owns its socket and re-dispatches the nudge).
    try { window.__ebRealtime && window.__ebRealtime.connect(); } catch (e) {}
    check();
    setInterval(check, 30000);     // safety net when the socket is down
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) check(); });
    window.addEventListener("launchpad:realtime:inapp_message", function (ev) {
      var d = (ev && ev.detail) || {};
      if (d.kind === "announcement") check();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

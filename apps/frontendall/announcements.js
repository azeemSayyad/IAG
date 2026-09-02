/*
 * Agent announcement gate (loaded on every portal page + the /sms app).
 *
 * Polls the signed-in agent's unacknowledged announcements. While one is pending
 * it covers the whole screen with a blurred, click-blocking overlay and a popup —
 * the agent can do NOTHING until they tick "I have seen this and agree" and press
 * Received, which acks it (and unblurs). Admins/non-agents get nothing (the
 * backend returns no pending for them). Self-contained: no __ebAPI / framework
 * dependency, so the one file works on the vanilla portal AND the React /sms app.
 */
;(function () {
  "use strict";
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
    return fetch(API + path, { headers: authHeaders({}) })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }
  function ackPost(id) {
    return fetch(API + "/announcements/" + encodeURIComponent(id) + "/ack",
      { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: "{}" })
      .then(function (r) { return r.ok; })
      .catch(function () { return false; });
  }

  var showing = false;
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }

  function render(ann) {
    if (document.getElementById("annOverlay")) return;
    showing = true;
    var ov = document.createElement("div");
    ov.id = "annOverlay";
    ov.setAttribute("style",
      "position:fixed;inset:0;z-index:2147483647;display:flex;align-items:center;justify-content:center;" +
      "background:rgba(15,23,42,.45);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px)");
    ov.innerHTML =
      '<div role="dialog" aria-modal="true" style="max-width:560px;width:92%;background:#fff;border-radius:16px;' +
      'box-shadow:0 24px 70px rgba(0,0,0,.35);padding:26px 28px;font-family:Inter,system-ui,-apple-system,sans-serif">' +
        '<div style="font-size:.72rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#C97B3A;margin-bottom:10px">' +
          '📣 Announcement</div>' +
        '<div style="font-size:1.05rem;line-height:1.55;color:#0F172A;white-space:pre-wrap;font-weight:500">' + esc(ann.body) + '</div>' +
        '<label style="display:flex;align-items:center;gap:10px;margin:22px 0 16px;cursor:pointer;font-size:.9rem;color:#334155">' +
          '<input id="annAgree" type="checkbox" style="width:18px;height:18px;cursor:pointer">' +
          ' I have seen this announcement and agree.</label>' +
        '<button id="annReceived" type="button" disabled style="width:100%;height:46px;border:none;border-radius:10px;' +
          'background:#9CA3AF;color:#fff;font:700 .95rem Inter,system-ui,sans-serif;cursor:not-allowed">Received</button>' +
      "</div>";
    document.body.appendChild(ov);
    var cb = ov.querySelector("#annAgree"), btn = ov.querySelector("#annReceived");
    cb.addEventListener("change", function () {
      btn.disabled = !cb.checked;
      btn.style.background = cb.checked ? "#16A34A" : "#9CA3AF";
      btn.style.cursor = cb.checked ? "pointer" : "not-allowed";
    });
    btn.addEventListener("click", function () {
      if (!cb.checked) return;
      btn.disabled = true; btn.textContent = "Saving…";
      ackPost(ann.id).then(function () {
        var el = document.getElementById("annOverlay"); if (el) el.remove();
        showing = false;
        check();   // show the next pending announcement, if any
      });
    });
    cb.focus();
  }

  function check() {
    if (showing || !token()) return;
    get("/announcements/pending").then(function (r) {
      var list = (r && r.pending) || [];
      if (list.length) render(list[0]);
    });
  }

  function start() {
    if (!token()) return;          // not signed in -> nothing to gate
    check();
    setInterval(check, 30000);     // pick up new announcements within ~30s, no refresh needed
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

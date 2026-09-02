;(function(){
  'use strict';

  // Shared error boundary — provides retry overlay + toast across all pages.
  //   window.__ebShowError(msg, retryFn) — shows blocking error overlay
  //   window.__ebShowToast(msg, kind)    — shows non-blocking toast
  //   window.__ebHideError()             — hides error overlay
  //   window.__ebDismissError()          — alias for hide
  //   window.__ebShowLoading()           — shows full-page spinner overlay
  //   window.__ebHideLoading()           — hides full-page spinner overlay
  //   window.__ebSetLoading(el, state)   — sets loading state on a specific element
  //   window.__ebSafeInit(fn)            — wraps async init with retry overlay

  var overlay = null;
  var toastEl = null;
  var spinnerEl = null;

  // Inject skeleton CSS once
  var skeletonStyle = document.createElement('style');
  skeletonStyle.textContent =
    '@keyframes ebShimmer{0%{background-position:-400px 0}100%{background-position:400px 0}}' +
    '.eb-skeleton{background:linear-gradient(90deg,var(--bg-input,#eee) 25%,var(--bg-card-hover,#f5f5f5) 50%,var(--bg-input,#eee) 75%);background-size:800px 100%;animation:ebShimmer 1.4s ease-in-out infinite;border-radius:6px;display:inline-block}' +
    '.eb-loading-spinner{display:inline-block;width:20px;height:20px;border:2px solid var(--border,rgba(0,0,0,.15));border-top-color:var(--accent,#C97B3A);border-radius:50%;animation:ebSpin .6s linear infinite;vertical-align:middle}' +
    '@keyframes ebSpin{to{transform:rotate(360deg)}}' +
    '.eb-loading-overlay{position:fixed;inset:0;z-index:99997;background:var(--bg-page, rgba(255,255,255,.7));display:flex;align-items:center;justify-content:center;flex-direction:column;gap:16px;backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px);transition:opacity .3s}' +
    '.eb-loading-overlay .eb-loading-spinner{width:36px;height:36px;border-width:3px}' +
    '.eb-loading-overlay .eb-loading-label{font-size:.875rem;color:var(--text-faint,#888);font-family:-apple-system,BlinkMacSystemFont,sans-serif}';
  document.head.appendChild(skeletonStyle);

  function init(){
    if (document.getElementById('ebErrorBoundary')) return;

    var ebHTML =
      '<div id="ebErrorBoundary" style="display:none;position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.55);backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;flex-direction:column;padding:24px;font-family:-apple-system,BlinkMacSystemFont,sans-serif">' +
        '<div style="background:var(--bg-card,#fff);border-radius:16px;padding:32px 40px;max-width:420px;width:100%;text-align:center;box-shadow:0 8px 40px rgba(0,0,0,.18)">' +
          '<div style="font-size:48px;margin-bottom:12px;color:var(--danger,#D03A3A)">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="width:48px;height:48px;display:inline-block;vertical-align:middle">' +
              '<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>' +
            '</svg>' +
          '</div>' +
          '<div id="ebErrorTitle" style="font-size:1.125rem;font-weight:600;margin-bottom:8px;color:var(--text,#1a1a1a)">Something went wrong</div>' +
          '<div id="ebErrorMsg" style="font-size:.875rem;color:var(--text-faint,#888);line-height:1.5;margin-bottom:24px"></div>' +
          '<div style="display:flex;gap:12px;justify-content:center">' +
            '<button id="ebErrorRetryBtn" type="button" style="padding:10px 24px;border-radius:8px;border:none;background:var(--accent,#C97B3A);color:#fff;font-size:.875rem;font-weight:500;cursor:pointer">Retry</button>' +
            '<button id="ebErrorDismissBtn" type="button" style="padding:10px 24px;border-radius:8px;border:none;background:var(--bg-input,#f0f0f0);color:var(--text,#1a1a1a);font-size:.875rem;cursor:pointer">Dismiss</button>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div id="ebToast" style="position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:99998;background:var(--bg-card,#1a1a1a);color:var(--text,#fff);padding:10px 20px;border-radius:10px;font-size:.8125rem;font-weight:500;font-family:-apple-system,BlinkMacSystemFont,sans-serif;box-shadow:0 4px 20px rgba(0,0,0,.25);display:none;align-items:center;gap:10px;max-width:90vw;pointer-events:auto"></div>' +
      '<div id="ebSpinner" class="eb-loading-overlay" style="display:none">' +
        '<div class="eb-loading-spinner"></div>' +
        '<div class="eb-loading-label">Loading…</div>' +
      '</div>';

    var div = document.createElement('div');
    div.innerHTML = ebHTML;
    document.body.appendChild(div.firstElementChild);

    overlay = document.getElementById('ebErrorBoundary');
    overlay.style.display = 'none';

    toastEl = document.getElementById('ebToast');
    spinnerEl = document.getElementById('ebSpinner');

    document.getElementById('ebErrorRetryBtn').addEventListener('click', function(){
      var fn = window.__ebRetryFn;
      window.__ebHideError();
      if (typeof fn === 'function') fn();
    });

    document.getElementById('ebErrorDismissBtn').addEventListener('click', function(){
      window.__ebHideError();
    });
  }

  function showError(msg, retryFn){
    init();
    var titleEl = document.getElementById('ebErrorTitle');
    var msgEl = document.getElementById('ebErrorMsg');
    if (titleEl) titleEl.textContent = msg && msg.includes('offline') ? 'Network error' : 'Something went wrong';
    if (msgEl) msgEl.innerHTML = msg || 'An unexpected error occurred. Please try again.';
    window.__ebRetryFn = typeof retryFn === 'function' ? retryFn : null;
    hideLoading();
    overlay.style.display = 'flex';
  }

  function hideError(){
    if (overlay) overlay.style.display = 'none';
    window.__ebRetryFn = null;
  }

  function toast(msg, kind){
    init();
    if (!toastEl) return;
    toastEl.textContent = msg || '';
    toastEl.style.background = kind === 'danger' ? 'var(--danger,#D03A3A)' : kind === 'accent' ? 'var(--accent,#C97B3A)' : 'var(--bg-card,#1a1a1a)';
    toastEl.style.display = 'flex';
    clearTimeout(toast._t);
    toast._t = setTimeout(function(){ if (toastEl) toastEl.style.display = 'none'; }, 3000);
  }

  function showLoading(label){
    init();
    if (spinnerEl) {
      var labelEl = spinnerEl.querySelector('.eb-loading-label');
      if (labelEl) labelEl.textContent = label || 'Loading…';
      spinnerEl.style.display = 'flex';
    }
  }

  function hideLoading(){
    if (spinnerEl) spinnerEl.style.display = 'none';
  }

  function setLoading(el, state){
    if (!el) return;
    if (state) {
      el.dataset.ebPrevHtml = el.innerHTML;
      el.dataset.ebPrevMinH = el.style.minHeight || '';
      el.style.minHeight = '80px';
      el.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;padding:32px"><div class="eb-loading-spinner"></div></div>';
    } else {
      if (el.dataset.ebPrevHtml !== undefined) {
        el.innerHTML = el.dataset.ebPrevHtml;
        el.style.minHeight = el.dataset.ebPrevMinH || '';
        delete el.dataset.ebPrevHtml;
        delete el.dataset.ebPrevMinH;
      }
    }
  }

  // Wraps an async init function with session check + error-boundary retry.
  // Shows loading spinner during init, error overlay on failure.
  // Automatically redirects to login if the session is invalid.
  function safeInit(initFn){
    var retryCount = 0;
    function run(){
      showLoading();

      // Session check — if token exists, verify it's still valid
      var sessionPromise;
      if (localStorage.getItem('access_token')) {
        sessionPromise = window.__ebAPI && typeof window.__ebAPI.get === 'function'
          ? window.__ebAPI.get('/auth/me').then(function(u){
              if (!u || !u.email) throw new Error('Session expired');
              return u;
            }).catch(function(){
              localStorage.removeItem('access_token');
              localStorage.removeItem('refresh_token');
              window.location.href = 'login.html';
              throw new Error('Redirecting to login');
            })
          : Promise.resolve();
      } else {
        sessionPromise = Promise.resolve();
      }

      return sessionPromise.then(function(){
        var p = initFn();
        if (p && typeof p.then === 'function') {
          return p.then(function(val){
            hideLoading();
            return val;
          }).catch(function(err){
            hideLoading();
            retryCount++;
            if (retryCount <= 1) {
              showError(err && err.message ? err.message : 'Failed to load page data. Check your connection.', run);
            } else {
              showError('Still having trouble. Check your connection and try again.', run);
            }
          });
        }
        hideLoading();
        return p;
      }).catch(function(){
        // session check failed (redirect already happened or will happen)
      });
    }
    return run();
  }

  window.__ebShowError = showError;
  window.__ebHideError = hideError;
  window.__ebDismissError = hideError;
  window.__ebShowToast = toast;
  window.__ebShowLoading = showLoading;
  window.__ebHideLoading = hideLoading;
  window.__ebSetLoading = setLoading;
  window.__ebSafeInit = safeInit;

  // Auto-init on load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

// --- SMS Workspace sidebar link injector --------------------------------
// Self-contained + error-isolated: adds an "SMS" group to the portal sidebar
// pointing at the standalone SMS app (/sms/). Touches nothing else; if the
// sidebar markup ever changes, the try/catch keeps this silent and harmless.
;(function () {
  'use strict';
  function injectSmsNav() {
    try {
      var nav = document.querySelector('aside.sidebar nav.sb-nav');
      if (!nav || document.getElementById('sbSms')) return;

      // Role gating mirrors the backend + the React SMS shell (lib/auth):
      //   Lead Manager   → agents + dev only  (the /sms/#/queue page)
      //   SMS Manager    → manager-class + admin + dev
      //   SMS Monitoring → dev only
      var role = (localStorage.getItem('ebRole') || '').toLowerCase();
      var isDev = role === 'dev';
      var canManager = ['manager', 'head', 'tenant_admin', 'admin', 'super_admin', 'dev'].indexOf(role) !== -1;
      var canQueue = role === 'agent' || isDev;
      var isAdmin = ['tenant_admin', 'super_admin', 'admin', 'dev'].indexOf(role) !== -1;

      var items = [];
      if (canQueue) {
        items.push({ href: '/sms/#/queue', label: 'Lead Manager', svg: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 9h8M8 13h5"/>' });
      }
      if (canManager) {
        items.push({ href: '/sms/#/manager', label: 'SMS Manager', svg: '<circle cx="9" cy="7" r="4"/><path d="M3 21c0-3.5 3-6 6-6s6 2.5 6 6"/><path d="M16 3.5a4 4 0 0 1 0 7.5"/><path d="M22 21c0-3-2-5-5-5.5"/>' });
      }
      // Sales Dashboard (admin-only) is bundled into the SMS section, right under SMS Manager.
      if (isAdmin) {
        items.push({ href: '/sms/#/sales-dashboard', label: 'Sales Dashboard', svg: '<path d="M3 3v18h18"/><path d="M7 15l4-6 4 4 5-7"/>' });
      }
      // DID Fleet capacity dashboard (admin-only) — bundled in the SMS section under
      // Sales Dashboard, mirroring its placement/gating. Unlike the SPA links above this
      // is a static portal page, so its href is did-fleet.html (it gets the active class
      // on that page via the `here` check below).
      if (isAdmin) {
        items.push({ href: 'did-fleet.html', label: 'DID Fleet', svg: '<path d="M4.9 16.1a9 9 0 0 1 0-8.2"/><path d="M19.1 7.9a9 9 0 0 1 0 8.2"/><path d="M7.8 13.4a5 5 0 0 1 0-2.8"/><path d="M16.2 10.6a5 5 0 0 1 0 2.8"/><circle cx="12" cy="12" r="1.6"/><path d="M12 13.6V21"/>' });
      }
      if (isDev) {
        items.push({ href: '/sms/#/monitoring', label: 'SMS Monitoring', svg: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>' });
      }
      if (!items.length) return;

      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var body = items.map(function (it) {
        var active = it.href.toLowerCase() === here ? ' active' : '';
        return '<a class="sb-item' + active + '" href="' + it.href + '" aria-label="' + it.label + '">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' + it.svg + '</svg>' +
          '<span class="sb-tip">' + it.label + '</span></a>';
      }).join('');

      var group = document.createElement('div');
      group.className = 'sb-group open';
      group.id = 'sbSms';
      group.innerHTML =
        '<button class="sb-group-head" type="button">Leads' +
          '<svg class="sb-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>' +
        '</button>' +
        '<div class="sb-group-body">' + body + '</div>';

      // Collapsible, matching the portal's other groups.
      var head = group.querySelector('.sb-group-head');
      if (head) head.addEventListener('click', function () { group.classList.toggle('open'); });

      // Place just before the Workspaces group when present, else at the end.
      var workspaces = document.getElementById('sbWorkspaces');
      if (workspaces && workspaces.parentNode === nav) {
        nav.insertBefore(group, workspaces);
      } else {
        nav.appendChild(group);
      }

      // Move Appointments out of the Workspaces group into this "Leads" group,
      // next to Lead Manager. Runs after prefs-extras has finished positioning
      // the nav (this injector fires last), so it has the final say.
      var apptLink = nav.querySelector('a[href="appointments.html"]');
      var gbody = group.querySelector('.sb-group-body');
      if (apptLink && gbody) gbody.appendChild(apptLink);
    } catch (e) {
      /* never let nav injection break a page */
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectSmsNav);
  } else {
    injectSmsNav();
  }
})();

// --- Global SMS lead popup --------------------------------------------------
// Shows the "new lead offered" popup on EVERY portal page (deals, settings,
// dashboard, …), not only inside the standalone /sms app — so an agent who has
// joined the queue never misses an offer wherever they are. Driven by the
// realtime `sms:lead_assigned` event (forwarded by services/api.js). The event
// is emitted only to the targeted agent's room, so whoever receives it is the
// intended agent. Self-contained + error-isolated.
;(function () {
  'use strict';
  if (window.__ebSmsLeadPopupInit) return;
  window.__ebSmsLeadPopupInit = true;

  // The React /sms app renders its own popup — don't double up there. Also never
  // run on auth pages (login / password reset): the agent lead-poll below makes
  // an authenticated call that would 401 with a stale token and bounce back to
  // login.html, causing an infinite refresh loop.
  try {
    var __path = (location.pathname || '').toLowerCase();
    if (__path.indexOf('/sms') === 0) return;
    if (/(login|password-reset|reset-password|forgot)/.test(__path)) return;
  } catch (e) {}

  var current = null;
  var BREAK_REASONS = ['Lunch', 'Bathroom', 'Personal'];

  function beep() {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx();
      function tone(freq, start, dur) {
        var o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'sine'; o.frequency.value = freq;
        o.connect(g); g.connect(ctx.destination);
        var t = ctx.currentTime + start;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
        o.start(t); o.stop(t + dur + 0.02);
      }
      tone(880, 0, 0.3);
      tone(1175, 0.18, 0.35);
    } catch (e) { /* autoplay blocked until a gesture — harmless */ }
  }

  function injectStyles() {
    if (document.getElementById('ebSmsLeadStyles')) return;
    var css = ''
      + '.eb-sl-overlay{position:fixed;inset:0;background:rgba(10,14,22,.55);z-index:9500;display:none;align-items:center;justify-content:center;padding:20px;}'
      + '.eb-sl-overlay.show{display:flex;}'
      + '.eb-sl-card{background:#fff;color:#1A1F2A;width:min(630px,94vw);border-radius:20px;box-shadow:0 24px 60px rgba(0,0,0,.35);overflow:hidden;animation:ebSlPop .18s ease-out;}'
      + '@keyframes ebSlPop{from{transform:translateY(12px) scale(.98);opacity:0}to{transform:none;opacity:1}}'
      + '.eb-sl-head{background:#F6EEE1;color:#1A1F2A;padding:22px 30px;display:flex;align-items:center;gap:12px;}'
      + '.eb-sl-head svg{width:26px;height:26px;flex-shrink:0;}'
      + '.eb-sl-head h3{margin:0;font-size:22px;font-weight:700;}'
      + '.eb-sl-body{padding:24px 30px 6px;display:flex;align-items:center;gap:18px;}'
      + '.eb-sl-avatar{width:72px;height:72px;border-radius:50%;background:#F6EEE1;color:#C97B3A;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;flex-shrink:0;letter-spacing:.02em;}'
      + '.eb-sl-name{font-size:30px;font-weight:700;line-height:1.15;}'
      + '.eb-sl-phone{font-size:22px;color:#5A6473;margin-top:4px;}'
      + '.eb-sl-msg{display:none;}'
      + '.eb-sl-foot{padding:22px 30px 27px;display:flex;flex-direction:column;gap:12px;}'
      + '.eb-sl-accept{width:100%;background:#16a34a;color:#fff;border:0;border-radius:15px;padding:19px;font-size:22px;font-weight:700;cursor:pointer;}'
      + '.eb-sl-row{display:flex;gap:12px;}'
      + '.eb-sl-pass{flex:1;background:#fff;color:#5A6473;border:1px solid #D7DBE0;border-radius:15px;padding:16px;font-size:21px;font-weight:700;cursor:pointer;}'
      + '.eb-sl-nw{flex:1;background:#fff;color:#DC2626;border:1px solid rgba(220,38,38,.5);border-radius:15px;padding:16px;font-size:21px;font-weight:700;cursor:pointer;}'
      + '.eb-sl-reasons{display:none;background:#fff;border-top:1px solid #EEF0F2;padding:18px 30px 24px;}'
      + '.eb-sl-reasons.show{display:block;}'
      + '.eb-sl-reasons-t{text-align:center;font-size:19px;font-weight:800;color:#5A6473;margin-bottom:14px;}'
      + '.eb-sl-reasons-grid{display:flex;flex-wrap:wrap;justify-content:center;gap:10px;}'
      + '.eb-sl-reason{border:1px solid #D7DBE0;background:#fff;color:#1A1F2A;border-radius:999px;padding:9px 18px;font-size:16px;font-weight:600;cursor:pointer;}'
      + '.eb-sl-reason.active{background:#F3F4F6;}'
      + '.eb-sl-other{display:none;gap:10px;margin-top:14px;}'
      + '.eb-sl-other.show{display:flex;}'
      + '.eb-sl-other input{flex:1;min-width:0;border:1px solid #D7DBE0;border-radius:12px;padding:12px 14px;font-size:16px;color:#1A1F2A;outline:none;}'
      + '.eb-sl-other button{background:#1A1F2A;color:#fff;border:0;border-radius:12px;padding:12px 20px;font-size:16px;font-weight:700;cursor:pointer;}'
      + '.eb-sl-cancel{display:block;width:100%;text-align:center;margin-top:15px;background:none;border:0;color:#9c5e23;font-size:19px;font-weight:600;cursor:pointer;}'
      + '.eb-sl-accept:disabled,.eb-sl-pass:disabled,.eb-sl-nw:disabled,.eb-sl-reason:disabled{opacity:.5;cursor:default;}';
    var s = document.createElement('style');
    s.id = 'ebSmsLeadStyles'; s.textContent = css;
    document.head.appendChild(s);
  }

  function build() {
    if (document.getElementById('ebSmsLeadOverlay')) return;
    var ov = document.createElement('div');
    ov.id = 'ebSmsLeadOverlay';
    ov.className = 'eb-sl-overlay';
    ov.innerHTML = ''
      + '<div class="eb-sl-card" role="dialog" aria-modal="true" aria-label="New lead">'
      +   '<div class="eb-sl-head"><svg viewBox="0 0 24 24" fill="none" stroke="#9c5e23" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.5"/><path d="M3.5 20c0-3.3 2.7-6 6-6"/><path d="M17 9v6M14 12h6"/></svg><h3>New lead</h3></div>'
      +   '<div class="eb-sl-body">'
      +     '<div class="eb-sl-avatar" id="ebSlAvatar">—</div>'
      +     '<div>'
      +       '<div class="eb-sl-name" id="ebSlName">—</div>'
      +       '<div class="eb-sl-phone" id="ebSlPhone">—</div>'
      +       '<div class="eb-sl-msg" id="ebSlMsg"></div>'
      +     '</div>'
      +   '</div>'
      +   '<div class="eb-sl-foot">'
      +     '<button class="eb-sl-accept" id="ebSlAccept">Accept lead</button>'
      +     '<div class="eb-sl-row">'
      +       '<button class="eb-sl-pass" id="ebSlPass">Pass</button>'
      +       '<button class="eb-sl-nw" id="ebSlNw">Not working</button>'
      +     '</div>'
      +   '</div>'
      +   '<div class="eb-sl-reasons" id="ebSlReasons">'
      +     '<div class="eb-sl-reasons-t">Choose a reason</div>'
      +     '<div class="eb-sl-reasons-grid" id="ebSlReasonGrid"></div>'
      +     '<div class="eb-sl-other"><input id="ebSlOther" type="text" placeholder="Other — type a reason…"/><button id="ebSlOtherGo">Go</button></div>'
      +   '</div>'
      + '</div>';
    document.body.appendChild(ov);
    document.getElementById('ebSlAccept').addEventListener('click', accept);
    document.getElementById('ebSlPass').addEventListener('click', pass);
    document.getElementById('ebSlNw').addEventListener('click', function () {
      var ow = document.querySelector('.eb-sl-other'); if (ow) ow.classList.remove('show');
      document.querySelectorAll('.eb-sl-reason.active').forEach(function (b) { b.classList.remove('active'); });
      var oi = document.getElementById('ebSlOther'); if (oi) oi.value = '';
      document.getElementById('ebSlReasons').classList.add('show');
    });
    var grid = document.getElementById('ebSlReasonGrid');
    BREAK_REASONS.forEach(function (r) {
      var b = document.createElement('button');
      b.className = 'eb-sl-reason';
      b.textContent = r;
      b.addEventListener('click', function () { notWorking(r); });
      grid.appendChild(b);
    });
    // "Other" pill — reveals a type-it-out field only when pressed.
    var otherInput = document.getElementById('ebSlOther');
    var otherGo = document.getElementById('ebSlOtherGo');
    var otherWrap = document.querySelector('.eb-sl-other');
    var otherPill = document.createElement('button');
    otherPill.className = 'eb-sl-reason';
    otherPill.textContent = 'Other';
    otherPill.addEventListener('click', function () {
      otherPill.classList.add('active');
      if (otherWrap) otherWrap.classList.add('show');
      if (otherInput) otherInput.focus();
    });
    grid.appendChild(otherPill);
    function submitOther() { var v = (otherInput.value || '').trim(); if (v) notWorking(v); }
    if (otherGo) otherGo.addEventListener('click', submitOther);
    if (otherInput) otherInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') submitOther(); });
  }

  function hide() {
    var ov = document.getElementById('ebSmsLeadOverlay');
    if (ov) ov.classList.remove('show');
    var rs = document.getElementById('ebSlReasons');
    if (rs) rs.classList.remove('show');
    current = null;
  }

  function setBusy(b) {
    ['ebSlAccept', 'ebSlPass', 'ebSlNw'].forEach(function (idv) {
      var el = document.getElementById(idv);
      if (el) el.disabled = b;
    });
    var grid = document.getElementById('ebSlReasonGrid');
    if (grid) {
      var rs = grid.querySelectorAll('.eb-sl-reason');
      for (var i = 0; i < rs.length; i++) rs[i].disabled = b;
    }
  }

  function accept() {
    if (!current || !window.__ebAPI) { hide(); return; }
    setBusy(true);
    window.__ebAPI.post('/sms/queue/accept/' + current.id).then(function () {
      location.href = '/sms/#/queue'; // land in the chat to work the lead
    }).catch(function () { setBusy(false); });
  }

  function pass() {
    if (!current || !window.__ebAPI) { hide(); return; }
    setBusy(true);
    window.__ebAPI.post('/sms/queue/pass/' + current.id).then(hide, hide);
  }

  // NOT WORKING: release the offered lead, then put the agent on a break with
  // the chosen reason so they stop being offered leads.
  function notWorking(reason) {
    if (!current || !window.__ebAPI) { hide(); return; }
    setBusy(true);
    window.__ebAPI.post('/sms/queue/pass/' + current.id).then(function () {
      return window.__ebAPI.post('/sms/queue/break/start', { reason: reason });
    }).then(hide, hide);
  }

  function showLead(lead) {
    if (!lead || !lead.id) return;
    var isNew = !current || current.id !== lead.id;
    build();
    current = lead;
    document.getElementById('ebSlName').textContent = lead.customer_name || lead.phone_number || 'New lead';
    document.getElementById('ebSlPhone').textContent = lead.phone_number || '';
    (function(){ var n=(lead.customer_name||'').trim(); var p=n?n.split(/\s+/):[]; var ini=p.length?(((p[0][0]||'')+(p.length>1?(p[p.length-1][0]||''):'')).toUpperCase()):'•'; document.getElementById('ebSlAvatar').textContent = ini||'•'; })();
    var msg = document.getElementById('ebSlMsg');
    // Customer message preview intentionally hidden so agents start fresh.
    if (msg) msg.style.display = 'none';
    setBusy(false);
    if (isNew) {
      var rs = document.getElementById('ebSlReasons');
      if (rs) rs.classList.remove('show'); // start on the action view for a new lead
    }
    document.getElementById('ebSmsLeadOverlay').classList.add('show');
    if (isNew) beep();  // only chime when a NEW lead appears, not on every poll
  }

  function onAssigned(e) {
    showLead((e && e.detail) || {});
  }

  // The live sms:lead_assigned event only fires once, on the page the agent was
  // on at offer time. Poll /sms/queue/current so the blocking popup FOLLOWS the
  // agent onto whatever page they navigate to, and stays until they act.
  function checkPendingOffer() {
    if (!window.__ebAPI || !window.__ebAPI.get) return;
    window.__ebAPI.get('/sms/queue/current', { _t: Date.now() }).then(function (r) {
      var lead = r && r.lead;
      if (lead && lead.status === 'ASSIGNED') showLead(lead);
      else hide();  // offer accepted/passed/withdrawn — clear the popup
    }).catch(function () { /* transient; next tick retries */ });
  }

  function isAgent() {
    try { return (localStorage.getItem('ebRole') || '').toLowerCase() === 'agent'; } catch (e) { return false; }
  }

  function onPing(e) {
    var p = (e && e.detail) || {};
    var msg = '🔔 ' + (p.message || 'A manager pinged you about a lead.') + (p.phone_number ? ' (' + p.phone_number + ')' : '');
    try { if (window.__ebShowToast) window.__ebShowToast(msg, 'pending'); } catch (_) {}
    beep();
  }

  function init() {
    injectStyles();
    window.addEventListener('launchpad:realtime:sms:lead_assigned', onAssigned);
    window.addEventListener('launchpad:realtime:sms:ping', onPing);
    try { window.__ebRealtime && window.__ebRealtime.connect(); } catch (e) {}
    // Agents in the queue: surface any pending offer on load, then keep checking
    // so it bumps over every page until they Accept/Pass.
    if (isAgent()) {
      checkPendingOffer();
      setInterval(checkPendingOffer, 6000);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

// --- Sales Dashboard nav link (admin-only) ----------------------------------
// Sales Dashboard now lives INSIDE the SMS group (see injectSmsNav above), bundled
// directly under SMS Manager — it is no longer injected as a separate top-level item.

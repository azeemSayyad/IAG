/*
 * Call-now popup — shared across agent pages.
 *
 * When a lead picks "Call now" over SMS, the backend fires a realtime
 * `call_now_request` event (to the targeted agent's room AND the tenant). This
 * script shows a prominent popup with the lead's details to the AGENT it was
 * routed to, so they can call the lead from their own phone. There is NO
 * click-to-call control by design — the agent dials manually.
 *
 * Include on every agent-facing page (after services/api.js):
 *   <script src="services/call-now-popup.js?v=1"></script>
 */
(function () {
  if (window.__ebCallNowInit) return;        // guard against double-include
  window.__ebCallNowInit = true;

  var MANAGER_ROLES = ['tenant_admin', 'super_admin', 'head', 'lead', 'manager'];
  var myUserId = null;
  var queue = [];

  function role() {
    try { return localStorage.getItem('ebRole') || ''; } catch (_) { return ''; }
  }

  // Resolve the current user's id (matches payload.agent_user_id). Cached.
  function resolveMe() {
    try {
      var cached = localStorage.getItem('ebUserId');
      if (cached) { myUserId = cached; return Promise.resolve(cached); }
    } catch (_) {}
    if (!window.__ebAPI || !window.__ebAPI.get) return Promise.resolve(null);
    return window.__ebAPI.get('/auth/me').then(function (u) {
      myUserId = (u && u.id) ? String(u.id) : null;
      try { if (myUserId) localStorage.setItem('ebUserId', myUserId); } catch (_) {}
      return myUserId;
    }).catch(function () { return null; });
  }

  function injectStyles() {
    if (document.getElementById('ebCallNowStyles')) return;
    var css = ''
      + '.eb-cn-overlay{position:fixed;inset:0;background:rgba(10,14,22,.55);z-index:9000;display:none;align-items:center;justify-content:center;padding:20px;}'
      + '.eb-cn-overlay.show{display:flex;}'
      + '.eb-cn-card{background:#fff;color:#1A1F2A;width:min(420px,94vw);border-radius:16px;box-shadow:0 24px 60px rgba(0,0,0,.35);overflow:hidden;animation:ebCnPop .18s ease-out;}'
      + '@keyframes ebCnPop{from{transform:translateY(12px) scale(.98);opacity:0}to{transform:none;opacity:1}}'
      + '.eb-cn-head{background:linear-gradient(135deg,#1463FF,#0A3FB0);color:#fff;padding:16px 20px;display:flex;align-items:center;gap:10px;}'
      + '.eb-cn-head .eb-cn-dot{width:10px;height:10px;border-radius:50%;background:#36E27A;box-shadow:0 0 0 0 rgba(54,226,122,.7);animation:ebCnPulse 1.4s infinite;}'
      + '@keyframes ebCnPulse{0%{box-shadow:0 0 0 0 rgba(54,226,122,.7)}70%{box-shadow:0 0 0 12px rgba(54,226,122,0)}100%{box-shadow:0 0 0 0 rgba(54,226,122,0)}}'
      + '.eb-cn-head h3{margin:0;font-size:15px;font-weight:700;letter-spacing:.2px;}'
      + '.eb-cn-head .eb-cn-badge{margin-left:auto;background:rgba(255,255,255,.2);padding:2px 9px;border-radius:999px;font-size:12px;font-weight:700;display:none;}'
      + '.eb-cn-body{padding:18px 20px 8px;}'
      + '.eb-cn-name{font-size:22px;font-weight:800;line-height:1.15;}'
      + '.eb-cn-phone{font-size:26px;font-weight:800;color:#0A3FB0;margin:6px 0 2px;letter-spacing:.5px;user-select:all;}'
      + '.eb-cn-hint{font-size:12.5px;color:#5A6473;margin-bottom:14px;}'
      + '.eb-cn-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px;margin-bottom:6px;}'
      + '.eb-cn-item{display:flex;flex-direction:column;border-top:1px solid #EEF1F5;padding-top:8px;}'
      + '.eb-cn-k{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:#8A93A2;}'
      + '.eb-cn-v{font-size:14px;font-weight:600;color:#1A1F2A;}'
      + '.eb-cn-foot{padding:14px 20px 18px;display:flex;gap:10px;}'
      + '.eb-cn-btn{flex:1;border:0;border-radius:10px;padding:12px;font-size:14px;font-weight:700;cursor:pointer;}'
      + '.eb-cn-btn-primary{background:#1463FF;color:#fff;}'
      + '.eb-cn-btn-primary:hover{background:#0A3FB0;}';
    var s = document.createElement('style');
    s.id = 'ebCallNowStyles';
    s.textContent = css;
    document.head.appendChild(s);
  }

  function buildModal() {
    if (document.getElementById('ebCallNowOverlay')) return;
    var ov = document.createElement('div');
    ov.id = 'ebCallNowOverlay';
    ov.className = 'eb-cn-overlay';
    ov.innerHTML = ''
      + '<div class="eb-cn-card" role="dialog" aria-modal="true" aria-label="Call now request">'
      +   '<div class="eb-cn-head"><span class="eb-cn-dot"></span><h3>📞 Call this lead now</h3><span class="eb-cn-badge" id="ebCnBadge"></span></div>'
      +   '<div class="eb-cn-body">'
      +     '<div class="eb-cn-name" id="ebCnName">—</div>'
      +     '<div class="eb-cn-phone" id="ebCnPhone">—</div>'
      +     '<div class="eb-cn-hint">Call from your phone now — the lead is expecting it.</div>'
      +     '<div class="eb-cn-grid" id="ebCnGrid"></div>'
      +   '</div>'
      +   '<div class="eb-cn-foot"><button class="eb-cn-btn eb-cn-btn-primary" id="ebCnDone">Got it</button></div>'
      + '</div>';
    document.body.appendChild(ov);
    document.getElementById('ebCnDone').addEventListener('click', dismissCurrent);
  }

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function beep() {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx();
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine'; o.frequency.value = 880;
      o.connect(g); g.connect(ctx.destination);
      g.gain.setValueAtTime(0.001, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
      o.start(); o.stop(ctx.currentTime + 0.42);
    } catch (_) {}
  }

  function renderCurrent() {
    var ov = document.getElementById('ebCallNowOverlay');
    if (!ov || !queue.length) { if (ov) ov.classList.remove('show'); return; }
    var p = queue[0];
    document.getElementById('ebCnName').textContent = p.lead_name || 'Lead';
    document.getElementById('ebCnPhone').textContent = p.phone || '—';
    var rows = [];
    if (p.state) rows.push(['State', p.state]);
    if (p.city) rows.push(['City', p.city]);
    if (p.email) rows.push(['Email', p.email]);
    if (p.score != null) rows.push(['Lead score', Math.round(Number(p.score))]);
    document.getElementById('ebCnGrid').innerHTML = rows.map(function (r) {
      return '<div class="eb-cn-item"><span class="eb-cn-k">' + esc(r[0]) + '</span><span class="eb-cn-v">' + esc(r[1]) + '</span></div>';
    }).join('');
    var badge = document.getElementById('ebCnBadge');
    if (queue.length > 1) { badge.style.display = 'inline-block'; badge.textContent = '+' + (queue.length - 1) + ' more'; }
    else { badge.style.display = 'none'; }
    ov.classList.add('show');
  }

  function dismissCurrent() {
    queue.shift();
    renderCurrent();
  }

  function show(p) {
    buildModal();
    queue.push(p);
    if (queue.length === 1) { beep(); }
    renderCurrent();
  }

  function onCallNow(e) {
    var p = (e && e.detail) || {};
    var targeted = p.agent_user_id && myUserId && String(p.agent_user_id) === String(myUserId);
    if (targeted) {
      show(p);
      return;
    }
    // Not for me: if I'm a manager/admin watching, surface a non-blocking toast.
    if (MANAGER_ROLES.indexOf(role()) !== -1 && window.__ebShowToast) {
      window.__ebShowToast('📞 Call-now: ' + (p.lead_name || 'a lead') + ' → ' + (p.agent_name || 'an agent'));
    }
  }

  function init() {
    injectStyles();
    resolveMe();
    window.addEventListener('launchpad:realtime:call_now_request', onCallNow);
    try { window.__ebRealtime && window.__ebRealtime.connect(); } catch (_) {}
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

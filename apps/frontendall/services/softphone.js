/* softphone.js — in-browser calling via Sinch Voice & Video (WebRTC).
 *
 * Responsibilities:
 *   - ask the backend whether calling is configured + whether THIS agent has a
 *     caller-ID number (GET /calls/config + POST /calls/webrtc-token)
 *   - load the Sinch in-app voice SDK and register the agent's browser client
 *   - place a call to a lead (POST /calls/dial → SDK callPhoneNumber)
 *   - render a floating in-call widget (lead name, live timer, mute, hang up)
 *
 * Degrades gracefully: if calling isn't configured, the SDK can't load, or the
 * agent has no number, the Call button explains why instead of breaking.
 *
 * Exposes window.__ebSoftphone = { ready, status(), call(opts), hangup(), reason() }.
 */
(function(){
  'use strict';
  var SDK_URL = 'https://cdn.sinch.com/latest/sinch-rtc-min.js'; // configurable
  var state = {
    configured: false, canCall: false, reason: 'init',
    client: null, callClient: null, token: null, callerNumber: null, activeCall: null, timer: null,
  };

  function api(){ return window.__ebAPI; }
  function role(){ try { return localStorage.getItem('ebRole') || ''; } catch(_){ return ''; } }

  function loadScript(src){
    return new Promise(function(res, rej){
      if(window.Sinch && window.Sinch.getSinchClientBuilder){ return res(true); }
      var s = document.createElement('script');
      s.src = src; s.async = true;
      s.onload = function(){ res(true); };
      s.onerror = function(){ rej(new Error('sdk_load_failed')); };
      document.head.appendChild(s);
    });
  }

  // ---- init: only for agents; resolves quietly with a reason when unavailable
  async function init(){
    state.reason = 'init';
    if(!api()){ state.reason = 'no_api'; return; }
    if(role() !== 'agent'){ state.reason = 'not_agent'; return; }
    try {
      var cfg = await api().get('/calls/config').catch(function(){ return null; });
      if(!cfg || !cfg.configured){ state.reason = 'not_configured'; return; }
      state.configured = true;
      var tok = await api().post('/calls/webrtc-token', {}).catch(function(){ return null; });
      if(!tok || !tok.token){ state.reason = 'no_token'; return; }
      state.token = tok; state.callerNumber = tok.caller_number;
      if(!tok.can_call){ state.reason = 'no_number'; return; }   // agent has no caller ID yet
      try {
        await loadScript(SDK_URL);
      } catch(e){ state.reason = 'sdk_unavailable'; return; }
      // Build + start the Sinch client per the In-app Calling JS SDK:
      //   Sinch.getSinchClientBuilder().applicationKey().environmentHost().userId().build()
      // then start() and supply our backend JWT in onCredentialsRequired.
      try {
        var Sinch = window.Sinch;
        state.client = Sinch.getSinchClientBuilder()
          .applicationKey(tok.appKey)
          .environmentHost('ocra.api.sinch.com')   // Sinch Ocra API host
          .userId(tok.identity)
          .build();
        await new Promise(function(resolve, reject){
          var done = false;
          state.client.addListener({
            onClientStarted: function(){ if(!done){ done = true; resolve(); } },
            onClientFailed: function(_c, err){ if(!done){ done = true; reject(err || new Error('client_failed')); } },
            // Called now and on every token refresh — always fetch a FRESH token.
            onCredentialsRequired: function(_c, registrationCallback){
              api().post('/calls/webrtc-token', {})
                .then(function(fresh){ registrationCallback((fresh && fresh.token) || tok.token); })
                .catch(function(){ registrationCallback(tok.token); });
            },
          });
          state.client.start();
        });
        // Outbound-call lifecycle events live on the call client.
        state.callClient = state.client.getCallClient();
        state.callClient.addListener({
          onOutgoingCallProgressing: function(){ setWidgetStatus('Ringing…'); },
          onOutgoingCallEstablished: function(){ setWidgetStatus('Connected'); startTimer(); },
          onOutgoingCallEnded:       function(){ closeWidget(); },
        });
        state.canCall = true; state.reason = 'ready';
      } catch(e){ state.reason = 'register_failed'; }
    } catch(e){ state.reason = 'error'; }
  }

  function toast(msg, bad){ if(window.__ebToast) window.__ebToast(msg, bad); }

  // ---- place a call to a lead ----
  async function call(opts){
    opts = opts || {};
    if(!state.configured){ toast('Calling is not set up yet', true); return; }
    if(!state.canCall && state.reason==='no_number'){ toast('No caller ID assigned to you — ask an admin', true); return; }
    if(!opts.leadId){ toast('No lead to call', true); return; }
    // 1) tell the backend (compliance + creates the call record)
    var dial;
    try {
      dial = await api().post('/calls/dial', { lead_id: opts.leadId, appointment_id: opts.appointmentId || null });
    } catch(e){
      toast('Call blocked: ' + ((e && e.message) || 'error'), true); return;
    }
    openWidget(dial.lead_name || opts.leadName || 'Lead', dial.to_number);
    // 2) place the actual WebRTC call via the SDK's call client. Single argument —
    //    the caller-ID/CLI is set server-side in our /calls/svaml SVAML response.
    //    Lifecycle (ringing/connected/ended) is handled by the callClient listener
    //    registered in init().
    try {
      if(state.callClient && state.callClient.callPhoneNumber){
        state.activeCall = state.callClient.callPhoneNumber(dial.to_number);
      } else {
        setWidgetStatus('Connecting…');
      }
    } catch(e){
      setWidgetStatus('Could not start audio');
    }
  }

  function hangup(){
    try { if(state.activeCall && state.activeCall.hangup) state.activeCall.hangup(); } catch(_){}
    closeWidget();
  }
  function mute(){
    try {
      if(state.activeCall){
        if(state.activeCall.isMuted && state.activeCall.isMuted()) state.activeCall.unmute && state.activeCall.unmute();
        else state.activeCall.mute && state.activeCall.mute();
      }
    } catch(_){}
  }

  // ---- floating in-call widget ----
  function openWidget(name, number){
    closeWidget();
    var w = document.createElement('div');
    w.id = 'ebSoftphone';
    w.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:9999;background:#1A1F2A;color:#fff;border-radius:14px;padding:16px 18px;min-width:240px;box-shadow:0 12px 40px rgba(0,0,0,.35);font-family:inherit';
    w.innerHTML =
      '<div style="font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);font-weight:700">On call</div>'+
      '<div style="font-size:1rem;font-weight:600;margin-top:4px">'+name+'</div>'+
      '<div style="font-size:.8rem;color:rgba(255,255,255,.7)">'+(number||'')+'</div>'+
      '<div id="ebSpStatus" style="font-size:.8rem;margin-top:8px;color:#9FE3B0">Calling…</div>'+
      '<div id="ebSpTimer" style="font-size:1.3rem;font-weight:700;margin:6px 0;font-variant-numeric:tabular-nums">00:00</div>'+
      '<div style="display:flex;gap:8px;margin-top:8px">'+
        '<button id="ebSpMute" type="button" style="flex:1;background:rgba(255,255,255,.12);color:#fff;border:none;border-radius:8px;padding:9px;font:600 .8rem inherit;cursor:pointer">Mute</button>'+
        '<button id="ebSpHang" type="button" style="flex:1;background:#A3525C;color:#fff;border:none;border-radius:8px;padding:9px;font:600 .8rem inherit;cursor:pointer">Hang up</button>'+
      '</div>';
    document.body.appendChild(w);
    document.getElementById('ebSpHang').addEventListener('click', hangup);
    document.getElementById('ebSpMute').addEventListener('click', mute);
  }
  function setWidgetStatus(s){ var el=document.getElementById('ebSpStatus'); if(el) el.textContent = s; }
  function startTimer(){
    var sec = 0; clearInterval(state.timer);
    state.timer = setInterval(function(){
      sec++; var m=String(Math.floor(sec/60)).padStart(2,'0'), s=String(sec%60).padStart(2,'0');
      var el=document.getElementById('ebSpTimer'); if(el) el.textContent = m+':'+s;
    }, 1000);
  }
  function closeWidget(){
    clearInterval(state.timer); state.timer=null; state.activeCall=null;
    var w=document.getElementById('ebSoftphone'); if(w) w.remove();
  }

  window.__ebSoftphone = {
    init: init,
    call: call,
    hangup: hangup,
    status: function(){ return { configured: state.configured, canCall: state.canCall, reason: state.reason, callerNumber: state.callerNumber }; },
    reason: function(){ return state.reason; },
  };

  // Auto-init after the API + DOM are ready (agents only; quiet no-op otherwise).
  function boot(){ if(window.__ebAPI){ init(); } else { setTimeout(boot, 400); } }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();

;;(function () {
  'use strict';

  var isFile = location.protocol === 'file:';
  var isLocal = isFile || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  var isDirectFrontend = location.port === '13000';
  var localApiUrl = isFile
    ? 'http://127.0.0.1:18000'
    : (isDirectFrontend ? location.protocol + '//' + location.hostname + ':18000' : '');
  var API_URL =
    window.LAUNCHPAD_API_URL ||
    (isLocal ? localApiUrl : (localStorage.getItem('ebApiUrl') || location.origin));

  // In-flight GET dedup map + response cache with TTL
  var inFlight = {};
  var responseCache = {};
  var CACHE_TTL = 30000; // 30 seconds

  function cacheKey(url) {
    return url;
  }

  function getCached(url) {
    var key = cacheKey(url);
    var entry = responseCache[key];
    if (entry && Date.now() - entry.ts < CACHE_TTL) {
      return entry.data;
    }
    delete responseCache[key];
    return null;
  }

  function setCached(url, data) {
    responseCache[cacheKey(url)] = { data: data, ts: Date.now() };
  }

  var api = {
    get: function (path, params) {
      var url = API_URL + '/api/v1' + path;
      if (params) {
        var qs = Object.keys(params)
          .filter(function (k) { return params[k] !== undefined && params[k] !== null; })
          .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); })
          .join('&');
        if (qs) url += '?' + qs;
      }
      // Check cache first
      var cached = getCached(url);
      if (cached) return Promise.resolve(cached);
      // Deduplicate in-flight GETs
      if (inFlight[url]) return inFlight[url];
      var p = makeRequest('GET', url).then(function(data){
        setCached(url, data);
        delete inFlight[url];
        return data;
      }).catch(function(err){
        delete inFlight[url];
        throw err;
      });
      inFlight[url] = p;
      return p;
    },

    post: function (path, body) {
      return makeRequest('POST', API_URL + '/api/v1' + path, body);
    },

    patch: function (path, body) {
      return makeRequest('PATCH', API_URL + '/api/v1' + path, body);
    },

    del: function (path) {
      return makeRequest('DELETE', API_URL + '/api/v1' + path);
    },

    // Invalidate the GET response cache so the next reads fetch fresh data.
    // Called automatically when realtime events arrive (see emitRealtimeEvent)
    // so live updates never render stale cached responses.
    clearCache: function () {
      responseCache = {};
    },

    upload: function (path, formData) {
      return makeUpload(API_URL + '/api/v1' + path, formData);
    },
  };

  function getTokens() {
    return {
      access_token: localStorage.getItem('access_token'),
      refresh_token: localStorage.getItem('refresh_token'),
    };
  }

  function setTokens(access, refresh) {
    if (access) localStorage.setItem('access_token', access);
    if (refresh) localStorage.setItem('refresh_token', refresh);
  }

  function clearTokens() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    // Also drop the cached role so stale agent/admin state can't drive
    // role-specific logic (e.g. the agent lead poll) after a session is lost.
    try { localStorage.removeItem('ebRole'); } catch (e) {}
  }

  // Bounce an unauthenticated request back to login — but NEVER when we're
  // already on the login page, otherwise a failed auth call there reloads it
  // forever (the infinite-refresh loop).
  function redirectToLogin() {
    try {
      if ((location.pathname || '').toLowerCase().indexOf('/login') !== -1) return;
    } catch (e) {}
    window.location.href = '/login.html';
  }

  function handleRefresh(url, method, headers, body) {
    var tokens = getTokens();
    if (tokens.refresh_token) {
      return fetch(API_URL + '/api/v1/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: tokens.refresh_token }),
      }).then(function (refreshRes) {
        if (refreshRes.ok) {
          return refreshRes.json().then(function (data) {
            setTokens(data.access_token, data.refresh_token);
            headers['Authorization'] = 'Bearer ' + data.access_token;
            return fetch(url, { method: method, headers: headers, body: body });
          });
        }
        clearTokens();
        redirectToLogin();
        throw new Error('Session expired');
      });
    }
    clearTokens();
    redirectToLogin();
    throw new Error('Unauthorized');
  }

  // Auth endpoints must NOT trigger the refresh-then-redirect flow: a 401 here
  // means "bad credentials / expired reset", not "session expired". Let it fall
  // through to a normal thrown error so the login page can show a clear message
  // instead of reloading (which looks like the page "refreshing" on bad login).
  function isAuthEndpoint(url) {
    return /\/auth\/(login|refresh|password-reset)/.test(url || '');
  }

  function handleResponse(res, url, method, headers, body) {
    if (res.status === 204) return null;
    if (res.status === 401 && !isAuthEndpoint(url)) {
      return handleRefresh(url, method, headers, body).then(function (retryRes) {
        return handleResponse(retryRes, url, method, headers, body);
      });
    }
    return res.json().then(function (data) {
      if (!res.ok) {
        var err = new Error(data.detail || data.message || 'Request failed');
        err.status = res.status;
        err.data = data;
        throw err;
      }
      return data;
    });
  }

  function makeRequest(method, url, body) {
    var tokens = getTokens();
    var headers = { 'Content-Type': 'application/json' };
    if (tokens.access_token) {
      headers['Authorization'] = 'Bearer ' + tokens.access_token;
    }

    var opts = {
      method: method,
      headers: headers,
    };
    if (body) opts.body = JSON.stringify(body);

    return fetch(url, opts).then(function (res) {
      return handleResponse(res, url, method, headers, opts.body);
    });
  }

  function makeUpload(url, formData) {
    var tokens = getTokens();
    var headers = {};
    if (tokens.access_token) {
      headers['Authorization'] = 'Bearer ' + tokens.access_token;
    }

    var opts = {
      method: 'POST',
      headers: headers,
      body: formData,
    };

    return fetch(url, opts).then(function (res) {
      return handleResponse(res, url, 'POST', headers, formData);
    });
  }

  window.__ebAPI = api;
  window.__ebAPI.baseUrl = API_URL;
  try {
    document.documentElement.setAttribute('data-eb-api', 'ready');
  } catch(e) {}

  var realtime = {
    socket: null,
    loading: false,
    connected: false,
    connect: connectRealtime,
    disconnect: disconnectRealtime,
  };
  window.__ebRealtime = realtime;
  try {
    document.documentElement.setAttribute('data-eb-realtime', 'ready');
  } catch(e) {}

  function emitRealtimeEvent(name, detail) {
    try {
      // Data changed somewhere — drop the GET cache so realtime-triggered
      // reloads fetch fresh data instead of a stale cached response.
      if (name !== 'connected' && name !== 'connect') { responseCache = {}; }
    } catch(e) {}
    try {
      window.dispatchEvent(new CustomEvent('launchpad:realtime', {
        detail: { type: name, data: detail || {} },
      }));
      window.dispatchEvent(new CustomEvent('launchpad:realtime:' + name, {
        detail: detail || {},
      }));
    } catch(e) {}
  }

  function loadSocketClient() {
    if (window.io) return Promise.resolve();
    if (window.__ebSocketClientPromise) return window.__ebSocketClientPromise;
    window.__ebSocketClientPromise = new Promise(function(resolve, reject) {
      var script = document.createElement('script');
      script.src = 'vendor/socket.io.min.js';
      script.async = true;
      script.onload = function(){ resolve(); };
      script.onerror = function(){ reject(new Error('Socket.IO client failed to load')); };
      document.head.appendChild(script);
    });
    return window.__ebSocketClientPromise;
  }

  function connectRealtime() {
    var tokens = getTokens();
    if (!tokens.access_token || realtime.loading || realtime.socket) return;
    realtime.loading = true;
    loadSocketClient().then(function() {
      if (!window.io || realtime.socket) return;
      var socket = window.io(API_URL, {
        path: '/socket.io',
        transports: ['websocket', 'polling'],
        auth: { token: tokens.access_token },
        reconnection: true,
        reconnectionAttempts: 10,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 10000,
      });
      realtime.socket = socket;
      [
        'connected',
        'notification',
        'booking_created',
        'booking_cancelled',
        'lead_created',
        'lead_updated',
        'lead_replied',
        'engage_cloud_inbound_processed',
        'conversation_message_created',
        'appointment_created',
        'call_now_request',
        'appointment_disposition_saved',
        'message_delivery_updated',
        'appointment_updated',
        'dashboard_updated',
        'analytics_updated',
        'state_license_created',
        'state_license_updated',
        'carrier_appointment_created',
        'carrier_appointment_updated',
        'carrier_appointments_imported',
        'deal_approved',
        'deal_not_approved',
        'appointment_expiring',
        'appointment_expired',
        'compliance_event_created',
        'compliance_event_resolved',
        'compliance_scan_completed',
        // SMS queue — so the lead popup + alerts work on every portal page,
        // not only inside the standalone /sms app.
        'sms:lead_assigned',
        'sms:new_message',
        'sms:queue_updated',
        'sms:ping',
        // In-app admin↔agent direct messaging (Inbox / Admin Inbox).
        'inapp_message',
      ].forEach(function(eventName) {
        socket.on(eventName, function(data) {
          emitRealtimeEvent(eventName, data);
        });
      });
      socket.on('connect', function() {
        realtime.connected = true;
        emitRealtimeEvent('socket_connected', { id: socket.id });
      });
      socket.on('disconnect', function(reason) {
        realtime.connected = false;
        emitRealtimeEvent('socket_disconnected', { reason: reason });
      });
      socket.on('connect_error', function(error) {
        realtime.connected = false;
        emitRealtimeEvent('socket_error', { message: error && error.message ? error.message : 'Connection failed' });
      });
    }).catch(function(err) {
      emitRealtimeEvent('socket_error', { message: err.message });
    }).finally(function() {
      realtime.loading = false;
    });
  }

  function disconnectRealtime() {
    if (realtime.socket) {
      realtime.socket.disconnect();
      realtime.socket = null;
    }
    realtime.connected = false;
  }

  if (getTokens().access_token) {
    connectRealtime();
  }
  window.addEventListener('storage', function(evt) {
    if (evt.key === 'access_token') {
      disconnectRealtime();
      if (evt.newValue) connectRealtime();
    }
  });

  // Draft backup helpers — persists wizard draft to the API for recovery
  var draftTimer = null;
  window.__ebSaveDraft = function(key, data) {
    try {
      var payload = { draft_key: key, draft_data: data, updated_at: new Date().toISOString() };
      api.post('/leads/draft', payload).catch(function(){ /* silent — fallback is sessionStorage */ });
    } catch(e) {}
  };
  window.__ebLoadDraft = function(key) {
    return api.get('/leads/draft?key=' + encodeURIComponent(key)).then(function(res){
      if (res && res.draft_data) {
        try { return typeof res.draft_data === 'string' ? JSON.parse(res.draft_data) : res.draft_data; }
        catch(e) { return res.draft_data; }
      }
      return null;
    }).catch(function(){ return null; });
  };
  window.__ebAutoSaveDraft = function(key, getDataFn, intervalMs) {
    if (draftTimer) clearInterval(draftTimer);
    intervalMs = intervalMs || 30000; // default 30s
    draftTimer = setInterval(function(){
      try {
        var data = typeof getDataFn === 'function' ? getDataFn() : getDataFn;
        if (data) window.__ebSaveDraft(key, data);
      } catch(e) {}
    }, intervalMs);
  };
  window.__ebStopAutoSave = function() {
    if (draftTimer) { clearInterval(draftTimer); draftTimer = null; }
  };
})();

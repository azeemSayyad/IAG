/* Background service worker — owns the ONE running job.
 *
 * State lives in chrome.storage.session (an MV3 worker can be stopped at any
 * time), so a restart picks up where it was. The HealthSherpa tab's content
 * script (hs.js) does the clicking; it asks here for the job and reports every
 * step here, and this worker forwards reports to the portal API with the
 * job's own token (never the agent's portal login).
 */
importScripts("common.js");

var HS_HOME = "https://www.healthsherpa.com/";
var STATE_KEY = "iagJob";
var LOG_KEY = "iagLog";

function getState() {
  return chrome.storage.session.get(STATE_KEY).then(function (r) { return r[STATE_KEY] || null; });
}
function setState(s) {
  var o = {}; o[STATE_KEY] = s;
  return chrome.storage.session.set(o);
}
function log(line) {
  return chrome.storage.session.get(LOG_KEY).then(function (r) {
    var arr = r[LOG_KEY] || [];
    arr.push(new Date().toLocaleTimeString() + "  " + line);
    var o = {}; o[LOG_KEY] = arr.slice(-80);
    return chrome.storage.session.set(o);
  });
}

function badge(text, color) {
  chrome.action.setBadgeText({ text: text || "" });
  if (color) chrome.action.setBadgeBackgroundColor({ color: color });
}

/* Report to the portal. Returns the server's answer ({stop: bool}) or null. */
function report(state, body) {
  if (!state || !state.apiBase) return Promise.resolve(null);
  var url = state.apiBase + "/hs-auth/jobs/" + encodeURIComponent(state.job.id) + "/report";
  var attempt = function (n) {
    return fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", "x-hs-job-token": state.job.token },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401 || r.status === 404) return { stop: true, status: "GONE" };
        if (!r.ok) throw new Error("HTTP " + r.status);
        return j;
      });
    }).catch(function (e) {
      if (n < 3) return new Promise(function (res) { setTimeout(res, 1500 * (n + 1)); }).then(function () { return attempt(n + 1); });
      log("Report failed: " + e.message);
      return null;
    });
  };
  return attempt(0);
}

/* Tell every portal tab (live progress on the page). */
function broadcast(update) {
  chrome.tabs.query({}, function (tabs) {
    tabs.forEach(function (t) {
      if (!t.url) return;
      try { if (!IAG.isPortalOrigin(new URL(t.url).origin)) return; } catch (e) { return; }
      chrome.tabs.sendMessage(t.id, { kind: "job-update", update: update }, function () { void chrome.runtime.lastError; });
    });
  });
}

var FINAL = ["AUTHORIZED", "NOT_FOUND", "NEEDS_REVIEW", "EXPIRED", "FAILED", "CANCELLED", "INVALID"];

function finish(state, status) {
  badge(status === "AUTHORIZED" ? "✓" : "", status === "AUTHORIZED" ? "#2f855a" : null);
  if (status === "AUTHORIZED") setTimeout(function () { badge(""); }, 15000);
  return setState(null);
}

/* Open (or reuse) the job's HealthSherpa tab. The job is SAVED with its tab id
   BEFORE the tab navigates: hs.js asks for its job as soon as the page loads,
   and a fast load used to ask before the save landed, get nothing, and leave
   the client stuck on "Opening HealthSherpa". */
function openHsTab(state) {
  return new Promise(function (resolve) {
    var go = function (tabId, windowId) {
      state.tabId = tabId;
      setState(state).then(function () {
        chrome.tabs.update(tabId, { url: HS_HOME, active: true }, function () {
          if (windowId) chrome.windows.update(windowId, { focused: true });
          resolve(tabId);
        });
      });
    };
    var create = function () {
      chrome.tabs.create({ url: "about:blank", active: true }, function (tab) { go(tab.id, tab.windowId); });
    };
    if (!state.tabId) return create();
    chrome.tabs.get(state.tabId, function (tab) {
      if (chrome.runtime.lastError || !tab) return create();
      go(tab.id, tab.windowId);
    });
  });
}

var lastTabId = null; // the tab we opened last time, reused for the next job

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  var kind = msg && msg.kind;

  if (kind === "start-job") {
    var origin = sender.origin || (sender.url ? new URL(sender.url).origin : "");
    if (!IAG.isPortalOrigin(origin)) { sendResponse({ ok: false, reason: "Not an IAG portal page." }); return; }
    var job = msg.job || {};
    if (!job.id || !job.token || !job.first_name || !job.last_name || !job.dob || !job.state) {
      sendResponse({ ok: false, reason: "The job is missing client details." }); return;
    }
    var apiBase = String(msg.apiBase || "");
    try {
      if (new URL(apiBase).origin !== origin) { sendResponse({ ok: false, reason: "Bad portal address." }); return; }
    } catch (e) { sendResponse({ ok: false, reason: "Bad portal address." }); return; }

    getState().then(function (cur) {
      if (cur && cur.job && cur.job.id !== job.id && !cur.stopped) {
        sendResponse({ ok: false, reason: "The extension is already running " + cur.job.first_name + " " + cur.job.last_name + ". Stop it first." });
        return;
      }
      chrome.storage.session.get("iagLastTab").then(function (r) {
        var state = {
          job: job, apiBase: apiBase, phase: "nav", tabId: r.iagLastTab || null,
          startedAt: Date.now(), linkSends: 0, searchTries: 0, stopped: false,
        };
        return openHsTab(state).then(function (tabId) {
          lastTabId = tabId;
          return chrome.storage.session.set({ iagLastTab: tabId });
        }).then(function () {
          badge("…", "#1f5fbf");
          log("Started " + job.first_name + " " + job.last_name);
          report(state, { step: "open", detail: "Opening HealthSherpa" });
          broadcast({ id: job.id, step: "open" });
          sendResponse({ ok: true });
        });
      });
    });
    return true; // async sendResponse
  }

  if (kind === "stop-job") {
    getState().then(function (s) {
      if (!s || (msg.id && s.job.id !== msg.id)) { sendResponse({ ok: true }); return; }
      s.stopped = true;
      setState(s).then(function () {
        log("Stopped: " + (msg.reason || ""));
        if (msg.fromExtension) report(s, { step: "cancelled", status: "CANCELLED", detail: msg.reason || "Stopped from the extension." });
        broadcast({ id: s.job.id, status: "CANCELLED" });
        if (s.tabId) chrome.tabs.sendMessage(s.tabId, { kind: "stop" }, function () { void chrome.runtime.lastError; });
        finish(s, "CANCELLED");
        sendResponse({ ok: true });
      });
    });
    return true;
  }

  if (kind === "focus-tab") {
    getState().then(function (s) {
      var id = (s && s.tabId) || lastTabId;
      if (!id) return;
      chrome.tabs.get(id, function (tab) {
        if (chrome.runtime.lastError || !tab) return;
        chrome.tabs.update(id, { active: true });
        chrome.windows.update(tab.windowId, { focused: true });
      });
    });
    return;
  }

  /* ---- from hs.js (the HealthSherpa tab) ---- */

  if (kind === "get-job") {
    getState().then(function (s) {
      var tabId = sender.tab && sender.tab.id;
      if (!s || s.stopped || s.tabId !== tabId) { sendResponse({ job: null }); return; }
      sendResponse({ state: s });
    });
    return true;
  }

  if (kind === "save") {
    // hs.js persists its phase/counters so a page load continues correctly.
    getState().then(function (s) {
      if (!s || s.job.id !== msg.id) { sendResponse({ ok: false }); return; }
      Object.assign(s, msg.patch || {});
      setState(s).then(function () { sendResponse({ ok: true }); });
    });
    return true;
  }

  if (kind === "report") {
    getState().then(function (s) {
      if (!s || s.job.id !== msg.id) { sendResponse({ stop: true }); return; }
      var body = msg.body || {};
      if (body.detail || body.step) log((body.status ? "[" + body.status + "] " : "") + (body.step || "") + " — " + (body.detail || ""));
      broadcast({ id: s.job.id, step: body.step, status: body.status, detail: body.detail, waiting_login: !!body.waiting_login });
      var serverBody = { step: body.step, status: body.status, detail: body.detail, hs_app_id: body.hs_app_id, link_sent: body.link_sent };
      report(s, serverBody).then(function (res) {
        var stop = !!(res && res.stop);
        if (body.status && FINAL.indexOf(body.status) !== -1) {
          finish(s, body.status);
          stop = true;
        } else if (stop) {
          // Stopped from the portal (or the job ended server-side).
          log("Portal says stop (" + (res.status || "") + ")");
          s.stopped = true;
          setState(s);
          finish(s, "CANCELLED");
        }
        sendResponse({ stop: stop });
      });
    });
    return true;
  }

  if (kind === "status") {
    getState().then(function (s) {
      chrome.storage.session.get(LOG_KEY).then(function (r) {
        sendResponse({ state: s ? { job: { first_name: s.job.first_name, last_name: s.job.last_name, id: s.job.id }, phase: s.phase, stopped: s.stopped } : null, log: r[LOG_KEY] || [], version: IAG.VERSION });
      });
    });
    return true;
  }
});

// The job's tab was closed -> the job can't continue.
chrome.tabs.onRemoved.addListener(function (tabId) {
  getState().then(function (s) {
    if (!s || s.tabId !== tabId || s.stopped) return;
    var waiting = s.phase === "link_sent";
    report(s, {
      step: "tab_closed",
      status: waiting ? "NEEDS_REVIEW" : "FAILED",
      detail: waiting
        ? "The HealthSherpa tab was closed while waiting for the client. The link may still work — check the client in HealthSherpa."
        : "The HealthSherpa tab was closed. Press Start to try again.",
    });
    broadcast({ id: s.job.id, status: waiting ? "NEEDS_REVIEW" : "FAILED" });
    finish(s, "FAILED");
  });
});

// Safety net: a job that went silent for 25 minutes (PC asleep, page hung).
chrome.alarms.create("iag-watchdog", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(function (a) {
  if (a.name !== "iag-watchdog") return;
  getState().then(function (s) {
    if (!s || s.stopped) return;
    var limitMs = (15 * ((s.job.max_resends || 0) + 1) + 10) * 60 * 1000;
    if (Date.now() - s.startedAt > limitMs) {
      report(s, { step: "timeout", status: "FAILED", detail: "Took too long — stopped. Press Start to try again." });
      broadcast({ id: s.job.id, status: "FAILED" });
      finish(s, "FAILED");
    }
  });
});

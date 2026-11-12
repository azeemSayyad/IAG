/* Runs inside HealthSherpa (only in the tab the extension opened for a job).
 *
 * Steps, as in the IAG authorization guide:
 *   1 Search Marketplace  2 first/last name, DOB, state, consent boxes, Search
 *   3 Plan Year 2027 -> Add to clients  4 Include consent -> Text link
 *   5/6 the CLIENT taps the link and authorizes on their own phone (never here)
 *   7 wait (Refresh status now and then)  8 Finish
 *
 * Everything is found by its VISIBLE text/label (HealthSherpa's markup isn't
 * ours and changes), each element is outlined before it's clicked so the agent
 * can follow, and anything unexpected STOPS with a clear reason rather than
 * guessing. A page load re-enters at the saved phase.
 */
(function () {
  if (window.top !== window) return;

  var STOPPED = { stopped: true };
  var DONE = { done: true };
  var stopFlag = false;
  var S = null;            // job state from the background
  var job = null;
  var lastStep = null;

  // ------------------------------------------------------------ messaging
  function send(msg) {
    return new Promise(function (resolve) {
      try {
        chrome.runtime.sendMessage(msg, function (res) {
          if (chrome.runtime.lastError) resolve(null); else resolve(res);
        });
      } catch (e) { resolve(null); }
    });
  }
  function save(patch) {
    Object.assign(S, patch);
    return send({ kind: "save", id: job.id, patch: patch });
  }
  chrome.runtime.onMessage.addListener(function (msg) {
    if (msg && msg.kind === "stop") { stopFlag = true; overlay.final("Stopped", "stop"); }
  });

  // ------------------------------------------------------------ utilities
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function norm(s) { return String(s || "").replace(/\s+/g, " ").trim(); }
  function digits(s) { return String(s || "").replace(/\D/g, ""); }
  function last10(s) { var d = digits(s); return d.length >= 10 ? d.slice(-10) : d; }
  function fmtPhone(d) { return d && d.length === 10 ? "(" + d.slice(0, 3) + ") " + d.slice(3, 6) + "-" + d.slice(6) : d; }
  function text(el) { return norm(el && (el.innerText || el.textContent)); }
  function visible(el) {
    if (!el || !el.isConnected) return false;
    var st = getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none") return false;
    return el.getClientRects().length > 0;
  }
  function checkStop() { if (stopFlag) throw STOPPED; }

  async function waitFor(fn, timeoutMs, everyMs) {
    var end = Date.now() + timeoutMs;
    while (true) {
      checkStop();
      var v = null;
      try { v = fn(); } catch (e) { v = null; }
      if (v) return v;
      if (Date.now() > end) return null;
      await sleep(everyMs || 400);
    }
  }

  function clickables(root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(
      "button, a, [role=button], input[type=submit], input[type=button]"));
  }
  function findButton(re, root) {
    var list = clickables(root).filter(visible);
    for (var i = 0; i < list.length; i++) {
      var t = text(list[i]) || norm(list[i].value) || norm(list[i].getAttribute("aria-label"));
      if (re.test(t)) return list[i];
    }
    return null;
  }
  function pageHas(re, root) { return re.test(text(root || document.body)); }

  var FIELD_TAGS = "input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), select, textarea, [role=combobox]";

  /* An <input>/<select> by its visible label (a <label>, or any short text
     element — real pages don't always use <label for>), then by its own
     name / id / autocomplete. Within a container holding several fields, the
     field that FOLLOWS the label wins (not just the container's first one). */
  function fieldByLabel(re, attrRe) {
    var labels = Array.prototype.slice.call(document.querySelectorAll("label, legend, span, div, p, strong"))
      .filter(function (el) {
        if (el.tagName !== "LABEL" && el.childElementCount > 2) return false;
        var t = norm(el.textContent);
        return t.length < 60 && re.test(t) && visible(el);
      });
    for (var i = 0; i < labels.length; i++) {
      var lab = labels[i];
      if (lab.htmlFor) { var byFor = document.getElementById(lab.htmlFor); if (byFor && visible(byFor)) return byFor; }
      if (lab.id) {
        var byAria = document.querySelector("[aria-labelledby~='" + lab.id.replace(/'/g, "") + "']");
        if (byAria && byAria.matches(FIELD_TAGS)) return byAria;
      }
      var inner = lab.querySelector(FIELD_TAGS);
      if (inner && visible(inner)) return inner;
      var p = lab.parentElement;
      for (var k = 0; k < 4 && p; k++, p = p.parentElement) {
        var cands = Array.prototype.slice.call(p.querySelectorAll(FIELD_TAGS)).filter(visible);
        if (!cands.length) continue;
        var after = cands.filter(function (c) { return lab.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING; });
        if (cands.length === 1) return cands[0];
        if (after.length) return after[0];
      }
    }
    var all = Array.prototype.slice.call(document.querySelectorAll(FIELD_TAGS)).filter(visible);
    for (var j = 0; j < all.length; j++) {
      var a = norm(all[j].getAttribute("aria-label") || all[j].getAttribute("placeholder"));
      if (a && re.test(a)) return all[j];
    }
    if (attrRe) {
      for (var m = 0; m < all.length; m++) {
        var at = [all[m].name, all[m].id, all[m].getAttribute("autocomplete"), all[m].getAttribute("data-testid")].join(" ");
        if (attrRe.test(at)) return all[m];
      }
    }
    return null;
  }

  /* A compact, value-free description of the page's form fields — sent with a
     failure report so the real HealthSherpa markup can be fixed against (we
     can't open HealthSherpa from outside the US). No client data in it. */
  function formSnapshot() {
    var out = ["url " + location.pathname];
    var heads = Array.prototype.slice.call(document.querySelectorAll("h1,h2,label")).filter(visible).slice(0, 25);
    out.push("labels: " + heads.map(function (h) { return h.tagName.toLowerCase() + (h.htmlFor ? "[for=" + h.htmlFor + "]" : "") + "“" + norm(h.textContent).slice(0, 40) + "”"; }).join(" | "));
    Array.prototype.slice.call(document.querySelectorAll(FIELD_TAGS + ", input[type=checkbox], [role=checkbox]")).filter(visible).slice(0, 20).forEach(function (el) {
      var bits = [el.tagName.toLowerCase()];
      ["type", "id", "name", "autocomplete", "placeholder", "aria-label", "aria-labelledby", "role", "class"].forEach(function (k) {
        var v = el.getAttribute(k); if (v) bits.push(k + "=" + String(v).slice(0, 40));
      });
      if (el.tagName === "SELECT") bits.push("options=" + el.options.length + " [" + Array.prototype.slice.call(el.options, 0, 4).map(function (o) { return norm(o.textContent) + "/" + o.value; }).join(",") + "…]");
      bits.push("filled=" + (el.value ? "yes" : "no"));
      out.push(bits.join(" "));
    });
    return out.join("\n").slice(0, 3800);
  }

  /* A checkbox by the text next to it (native or role=checkbox). */
  function checkboxByText(re, root) {
    var boxes = (root || document).querySelectorAll("input[type=checkbox], [role=checkbox]");
    for (var i = 0; i < boxes.length; i++) {
      var b = boxes[i];
      var t = "";
      if (b.labels && b.labels.length) t = norm(b.labels[0].textContent);
      if (!t && b.closest("label")) t = text(b.closest("label"));
      if (!t) { var p = b.parentElement; for (var k = 0; k < 3 && p && !re.test(t); k++, p = p.parentElement) t = text(p); }
      if (re.test(t)) return b;
    }
    return null;
  }
  function isChecked(b) { return b.checked === true || b.getAttribute("aria-checked") === "true"; }

  function setNative(el, v) {
    var proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
      : el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
  }
  function fire(el, type) { el.dispatchEvent(new Event(type, { bubbles: true })); }

  function viaTyping(el, value) {
    // The browser's own editing command: fires real (trusted) beforeinput /
    // input events, so React / Vue / masked fields all register it — setting
    // .value directly can look filled but be thrown away on the next render.
    try {
      el.focus();
      if (typeof el.select === "function") el.select();
      if (el.value) document.execCommand("delete", false, null);
      return document.execCommand("insertText", false, value);
    } catch (e) { return false; }
  }

  async function typeInto(el, value, sameAs) {
    await highlight(el);
    var same = function () { return sameAs ? sameAs(el.value) : norm(el.value).toLowerCase() === norm(value).toLowerCase(); };
    if (same()) return true;  // already right (e.g. a retry)
    viaTyping(el, value);
    await sleep(250);
    var ok = same();
    if (!ok) {
      el.focus();
      setNative(el, ""); fire(el, "input");
      setNative(el, value); fire(el, "input"); fire(el, "change");
      await sleep(200);
      ok = same();
    }
    if (!ok) {
      // A masked field (e.g. MM/DD/YYYY) — type it key by key instead.
      setNative(el, ""); fire(el, "input");
      for (var i = 0; i < value.length; i++) {
        var ch = value[i];
        el.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true }));
        setNative(el, el.value + ch);
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: ch, inputType: "insertText" }));
        el.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));
        await sleep(35);
      }
      fire(el, "change");
      await sleep(200);
      ok = sameAs ? sameAs(el.value) : norm(el.value).toLowerCase() === norm(value).toLowerCase();
    }
    el.blur(); fire(el, "blur");
    return ok;
  }

  function stateMatches(o, code, name) {
    var t = norm(o.textContent).toLowerCase(), v = String(o.value || "").toUpperCase();
    return v === code || t === name.toLowerCase() || t.toUpperCase() === code || t.indexOf(name.toLowerCase() + " ") === 0;
  }

  async function chooseState(el) {
    var code = job.state, name = job.state_name;
    if (el.tagName !== "SELECT") {
      // The label may point at a wrapper — use a real <select> inside it if there is one.
      var box = el.closest("div") || el.parentElement;
      var inner = box && box.querySelector("select");
      if (inner) el = inner;
    }
    if (el.tagName === "SELECT") {
      var opts = Array.prototype.slice.call(el.options);
      var cur = opts[el.selectedIndex];
      if (cur && stateMatches(cur, code, name)) return true;   // already chosen (HealthSherpa may preselect)
      var opt = opts.find(function (o) { return stateMatches(o, code, name); });
      if (!opt) return opts.length >= 10 ? "missing" : false;  // a short list = not the real state list
      await highlight(el);
      setNative(el, opt.value); fire(el, "input"); fire(el, "change");
      await sleep(200);
      return el.value === opt.value;
    }
    // A custom dropdown: open it, type, pick the option.
    await click(el);
    if (el.tagName === "INPUT") { await typeInto(el, name, function () { return true; }); }
    if (pageHas(new RegExp("^\\s*" + name + "\\s*$", "i"), el)) return true;  // already shows it
    var option = await waitFor(function () {
      var cands = document.querySelectorAll("[role=option], li, [class*=option]");
      for (var i = 0; i < cands.length; i++) if (visible(cands[i]) && norm(cands[i].textContent).toLowerCase() === name.toLowerCase()) return cands[i];
      return null;
    }, 4000);
    if (!option) return false;  // couldn't read a list — a person checks, never "bad data"
    await click(option);
    var box = el.closest("div") || el;
    return pageHas(new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), box.parentElement || box);
  }

  async function highlight(el) {
    if (!el) return;
    var target = visible(el) ? el : (el.closest("label") || el.parentElement || el);
    try { target.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (e) { /* old page */ }
    target.classList.add("iag-hl");
    await sleep(650);
    setTimeout(function () { target.classList.remove("iag-hl"); }, 900);
  }
  async function click(el) {
    checkStop();
    await highlight(el);
    checkStop();
    el.click();
    await sleep(400);
  }
  async function ensureChecked(box) {
    if (isChecked(box)) return true;
    await click(box);
    if (!isChecked(box) && box.labels && box.labels[0]) { box.labels[0].click(); await sleep(300); }
    return isChecked(box);
  }

  // ------------------------------------------------------------ reporting
  async function step(name, detail, extra) {
    checkStop();
    overlay.update(detail);
    if (name === lastStep && !(extra && (extra.link_sent || extra.hs_app_id || extra.diag))) return;
    lastStep = name;
    var body = Object.assign({ step: name, detail: detail }, extra || {});
    var res = await send({ kind: "report", id: job.id, body: body });
    if (res && res.stop && !(extra && extra.status)) { stopFlag = true; overlay.final("Stopped from the portal", "stop"); throw STOPPED; }
  }
  async function final(status, detail, extra) {
    var body = Object.assign({ step: "done", status: status, detail: detail }, extra || {});
    if (S.appId && !body.hs_app_id) body.hs_app_id = S.appId;
    await send({ kind: "report", id: job.id, body: body });
    overlay.final(detail, status === "AUTHORIZED" ? "ok" : (status === "NEEDS_REVIEW" || status === "LINK_SENT") ? "warn" : "bad", status);
    throw DONE;
  }

  // ------------------------------------------------------------ overlay
  var overlay = (function () {
    var box, msgEl, timerEl;
    function ensure() {
      if (box && box.isConnected) return;
      box = document.createElement("div");
      box.className = "iag-ov";
      box.innerHTML =
        '<div class="iag-ov-badge">IAG</div>' +
        '<div class="iag-ov-body"><div class="iag-ov-title"></div><div class="iag-ov-msg"></div></div>' +
        '<div class="iag-ov-timer"></div>' +
        '<button class="iag-ov-stop" type="button">Stop</button>';
      document.documentElement.appendChild(box);
      box.querySelector(".iag-ov-title").textContent = "IAG Assistant · " + job.first_name + " " + job.last_name;
      msgEl = box.querySelector(".iag-ov-msg");
      timerEl = box.querySelector(".iag-ov-timer");
      box.querySelector(".iag-ov-stop").addEventListener("click", function () {
        stopFlag = true;
        send({ kind: "stop-job", id: job.id, reason: "Stopped from the HealthSherpa tab.", fromExtension: true });
        overlay.final("Stopped", "stop");
      });
    }
    return {
      update: function (m) { ensure(); msgEl.textContent = m || ""; },
      timer: function (t) { ensure(); timerEl.textContent = t || ""; },
      final: function (m, tone, status) {
        ensure();
        box.classList.add("iag-tone-" + (tone || "ok"));
        var label = { AUTHORIZED: "Authorized ✓", NOT_FOUND: "Not found", NEEDS_REVIEW: "Needs your review",
          EXPIRED: "Link expired", FAILED: "Stopped — problem", INVALID: "Check the client's details" }[status] || "";
        msgEl.textContent = (label ? label + " — " : "") + (m || "");
        timerEl.textContent = "";
        var stopBtn = box.querySelector(".iag-ov-stop");
        stopBtn.textContent = "Close";
        stopBtn.onclick = function (e) { e.stopImmediatePropagation(); box.remove(); };
      },
    };
  })();

  // ------------------------------------------------------------ page detection
  function isLoginPage() {
    var pw = document.querySelector("input[type=password]");
    return (pw && visible(pw)) || pageHas(/log in to your account/i);
  }
  function isCodePage() {
    return pageHas(/authentication code|verification code|enter the code|receive a code/i) && !pageHas(/search for an application/i);
  }
  function isFfmPage() { return pageHas(/integrate your federally facilitated marketplace/i); }
  function isSearchPage() {
    return /person_search/.test(location.pathname + location.search) || pageHas(/search for an application/i);
  }
  function searchMarketplaceLink() { return findButton(/^search marketplace$/i); }
  function agentIdHere() {
    var q = new URLSearchParams(location.search).get("_agent_id");
    if (q) return q;
    var m = location.pathname.match(/^\/agents\/([^\/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }
  function searchUrl(agentId) {
    return location.origin + "/person_search?use_case=fetch" + (agentId ? "&_agent_id=" + encodeURIComponent(agentId) : "");
  }
  function isProfilePage() {
    return !!findButton(/^household profile$/i) && !!findButton(/^details$/i);
  }
  function authModal() {
    var heads = Array.prototype.slice.call(document.querySelectorAll("h1,h2,h3,h4,h5,[role=heading],[class*=title],[class*=header]"));
    // Fallback: any small leaf element with that exact text.
    Array.prototype.forEach.call(document.querySelectorAll("div,span,p,strong"), function (el) {
      if (el.childElementCount === 0 && /^(20\d\d authorization required|authorization complete)$/i.test(norm(el.textContent))) heads.push(el);
    });
    for (var i = 0; i < heads.length; i++) {
      var t = text(heads[i]);
      if (t.length > 80 || !visible(heads[i])) continue;
      if (/\b20\d\d authorization required\b/i.test(t) || /^authorization complete$/i.test(t)) {
        // The real dialog if it says so; otherwise the nearest container that
        // holds the popup's own buttons. (A bare [class*=modal] match is NOT
        // enough — the header bar is often "modal-header" and holds only ×.)
        return heads[i].closest("[role=dialog], [aria-modal=true]")
          || climbTo(heads[i], function (el) { return !!findButton(/^(refresh status|text link to.*|email link to.*|finish|resend by text)$/i, el); })
          || heads[i].parentElement;
      }
    }
    return null;
  }
  function climbTo(el, test) {
    var p = el;
    for (var k = 0; k < 8 && p; k++, p = p.parentElement) if (test(p)) return p;
    return null;
  }
  function collectFormErrors() {
    var els = document.querySelectorAll("[role=alert], [class*=error], [class*=Error], [class*=invalid], .help-block, .field-error");
    var out = [];
    for (var i = 0; i < els.length; i++) {
      if (!visible(els[i])) continue;
      var t = text(els[i]);
      // Only text that reads like a validation message counts.
      if (t && t.length < 200 && /required|must|invalid|enter|can.?t|cannot|format|punctuation|check|valid/i.test(t)
          && out.indexOf(t) === -1) out.push(t);
    }
    return out;
  }

  // ------------------------------------------------------------ login wait
  async function waitForLogin() {
    var why = isFfmPage()
      ? "HealthSherpa asks to connect your FFM (CMS) account — click Integrate account and finish the CMS sign-in. The extension continues by itself."
      : "Please log in to HealthSherpa in this tab (email, password and the code). The extension continues by itself.";
    await step("login", why, { waiting_login: true });
    overlay.update(why);
    var ok = await waitFor(function () {
      return !isLoginPage() && !isCodePage() && !isFfmPage() && (searchMarketplaceLink() || isSearchPage());
    }, 15 * 60 * 1000, 1000);
    if (!ok) await final("FAILED", "Wasn't logged in to HealthSherpa within 15 minutes.");
    lastStep = null;
  }

  // ------------------------------------------------------------ the flow
  async function run() {
    for (var guard = 0; guard < 6; guard++) {
      checkStop();
      if (isLoginPage() || isCodePage() || isFfmPage()) {
        if (S.phase === "link_sent" || S.phase === "finishing") {
          await waitForLogin();
          await final("NEEDS_REVIEW", "HealthSherpa logged you out while waiting for the client. Open the client in HealthSherpa to check the authorization.");
        }
        await waitForLogin();
        continue;
      }
      if (S.phase === "nav") {
        if (isSearchPage()) { await doSearch(); return; }
        await openSearch();
        continue;
      }
      if (S.phase === "searched") { await handleResults(true); return; }
      if (S.phase === "adding") { await handleAdd(); return; }
      if (S.phase === "link_sent") {
        if (authModal()) { await waitForClient(); return; }
        await final("NEEDS_REVIEW", "The page reloaded while waiting for the client. The link may still work — open the client in HealthSherpa to check.");
      }
      if (S.phase === "finishing") {
        await final("AUTHORIZED", "Authorized for " + job.plan_year + ".");
      }
      return;
    }
    await final("FAILED", "Couldn't get to HealthSherpa's search page.");
  }

  async function openSearch() {
    await step("search_page", "Opening Search Marketplace");
    var link = await waitFor(function () {
      return searchMarketplaceLink() || (isSearchPage() && "here")
        || ((agentIdHere() || S.agentId) && findButton(/^(dashboard|log out)$/i) && "go");
    }, 20000);
    if (link === "here") return;
    if (link === "go" || (!link && (agentIdHere() || S.agentId))) {
      // Logged in, but on a page without the menu (the public home page, which
      // is where healthsherpa.com/ lands an agent): go to the search page directly.
      var tries = (S.navTries || 0) + 1;
      if (tries > 3) await final("FAILED", "HealthSherpa keeps sending the search page somewhere else (" + location.pathname + ").", { diag: formSnapshot() });
      await save({ navTries: tries });
      location.href = searchUrl(agentIdHere() || S.agentId);
      await sleep(15000);  // the page load ends this script; the next one carries on
      return;
    }
    if (!link) {
      if (findButton(/^(sign in|log in)$/i)) { await waitForLogin(); return; }
      await final("FAILED", "Couldn't find “Search Marketplace” on HealthSherpa. Are you logged in as an agent?", { diag: formSnapshot() });
    }
    await click(link);
    // A full page load ends this script; an in-page route change keeps us here.
    await waitFor(isSearchPage, 15000);
  }

  async function doSearch() {
    await step("fill", "Filling client details");
    var tries = (S.searchTries || 0) + 1;
    if (tries > 3) await final("FAILED", "The search didn't go through after 3 tries.");
    await save({ searchTries: tries });

    var findForm = function () {
      var f = fieldByLabel(/^first name\b/i, /first.?name|given/i), l = fieldByLabel(/^last name\b/i, /last.?name|family|surname/i),
        d = fieldByLabel(/^date of birth\b/i, /birth|dob|bday/i) || document.querySelector("input[placeholder*='MM/DD' i]"),
        s = fieldByLabel(/^coverage state\b/i, /state/i);
      if (!(f && l && d && s) || f === l || f === d || l === d) return null;
      return { f: f, l: l, d: d, s: s };
    };
    var form = await waitFor(findForm, 15000);
    if (!form) await final("FAILED", "HealthSherpa's search form looks different — couldn't find the name / date of birth / state fields.", { diag: formSnapshot() });

    var dobDigits = digits(job.dob);
    var dobSame = function (v) { return digits(v) === dobDigits; };
    var nameSame = function (want) { return function (v) { return norm(v).toLowerCase() === norm(want).toLowerCase(); }; };
    var problems = [];
    var st = await chooseState(form.s);   // state first: changing it can re-render the form
    if (st === "missing") {
      await final("INVALID", "HealthSherpa's state list has no " + job.state_name + " (it may run its own state exchange).");
    }
    if (!st) problems.push("state");
    for (var round = 0; round < 3; round++) {
      form = findForm() || form;          // the fields may be new elements after a re-render
      var bad = [];
      if (!(await typeInto(form.f, job.first_name))) bad.push("first name");
      if (!(await typeInto(form.l, job.last_name))) bad.push("last name");
      if (!(await typeInto(form.d, job.dob, dobSame))) bad.push("date of birth");
      await sleep(600);
      form = findForm() || form;
      var lost = [];                      // filled a moment ago, empty now = the page threw it away
      if (!nameSame(job.first_name)(form.f.value)) lost.push("first name");
      if (!nameSame(job.last_name)(form.l.value)) lost.push("last name");
      if (!dobSame(form.d.value)) lost.push("date of birth");
      if (!lost.length && !bad.length) break;
      if (round === 2) { bad.concat(lost).forEach(function (x) { if (problems.indexOf(x) === -1) problems.push(x); }); }
    }

    var consent = checkboxByText(/received consent from this consumer/i);
    if (!consent) await final("FAILED", "Couldn't find the consent checkbox on the search page.");
    await step("consent", "Ticking “I received consent from this consumer”");
    if (!(await ensureChecked(consent))) problems.push("consent box");
    var older = checkboxByText(/include applications from plan years before/i);
    if (older) await ensureChecked(older);

    await save({ phase: "searched" });
    if (problems.length) {
      // Don't guess: let the agent finish the form; we carry on from the results.
      await step("fill", "Couldn't fill the " + problems.join(", ") + " — please fill it yourself, then click “Search the Marketplace”.",
        { diag: formSnapshot() });
      await handleResults(false, 5 * 60 * 1000);
      return;
    }
    var btn = findButton(/^search the marketplace$/i);
    if (!btn) await final("FAILED", "Couldn't find the “Search the Marketplace” button.");
    await click(btn);
    await handleResults(false);
  }

  /* Plan Year rows on the results page. */
  function readResults() {
    var btns = clickables().filter(function (b) { return visible(b) && /^(add to clients|view in dash)$/i.test(text(b)); });
    var rows = [];
    btns.forEach(function (b) {
      var row = b.closest("tr");
      if (!row) {
        var prev = b, p = b.parentElement;
        for (var k = 0; k < 8 && p; k++) {
          var n = clickables(p).filter(function (x) { return /^(add to clients|view in dash)$/i.test(text(x)); }).length;
          if (n > 1) break;
          prev = p;
          if (/Plan Year\s*\d{4}/i.test(text(p))) break;
          p = p.parentElement;
        }
        row = prev;
      }
      var t = row.innerText || row.textContent || "";
      var year = (t.match(/Plan Year\s*(\d{4})/i) || [])[1];
      var appId = (t.match(/\bID:\s*(\d+)/i) || [])[1];
      var dob = (t.match(/DOB:\s*(\d{4}-\d{2}-\d{2})/i) || [])[1];
      var name = (t.split("\n").map(norm).filter(function (x) { return x && !/^DOB/i.test(x); })[0]) || "";
      rows.push({ button: b, action: /view in dash/i.test(text(b)) ? "view" : "add", year: year ? +year : null,
        appId: appId || null, dob: dob || null, name: name, status: (t.match(/(Complete[^\n]*|Incomplete[^\n]*)/i) || [])[1] || "" });
    });
    return rows;
  }

  async function handleResults(afterReload, waitMs) {
    await step("search", "Searching the Marketplace");
    var since = Date.now();
    var outcome = await waitFor(function () {
      if (readResults().length) return "rows";
      if (pageHas(/no results found/i)) return "none";
      // Give the search 3 s before reading validation messages as the answer.
      if (!waitMs && Date.now() - since > 3000 && collectFormErrors().length && !pageHas(/search results/i)) return "errors";
      return null;
    }, waitMs || 45000, 500);

    if (!outcome) {
      if (afterReload && isSearchPage() && !pageHas(/search results/i)) {
        // The page reloaded before the search finished — search again.
        await save({ phase: "nav" });
        S.phase = "nav";
        await doSearch();
        return;
      }
      await final("FAILED", "HealthSherpa didn't show search results within " + Math.round((waitMs || 45000) / 1000) + " seconds.");
    }
    if (outcome === "errors") {
      await sleep(800);
      if (!readResults().length && !pageHas(/no results found/i)) {
        await final("INVALID", "HealthSherpa didn't accept the search: " + collectFormErrors().slice(0, 3).join(" · "));
      }
    }
    if (pageHas(/no results found/i) && !readResults().length) {
      await final("NOT_FOUND", "No results found. Check the spelling of the name, the date of birth and the state (one wrong letter gives no results) — or the client never had a Marketplace plan.");
    }

    await step("results", "Reading results");
    var rows = readResults();
    var target = rows.filter(function (r) { return r.year === job.plan_year; });
    if (!target.length) {
      var years = rows.map(function (r) { return r.year; }).filter(Boolean);
      years = years.filter(function (y, i) { return years.indexOf(y) === i; }).sort().reverse();
      await final("NOT_FOUND", "No Plan Year " + job.plan_year + " application" + (years.length ? " (found " + years.join(", ") + ")" : "") + ".");
    }
    var sameDob = target.filter(function (r) { return !r.dob || r.dob === job.dob_iso; });
    if (!sameDob.length) {
      await final("NOT_FOUND", "Plan Year " + job.plan_year + " found, but not with date of birth " + job.dob + ".");
    }
    var people = {};
    sameDob.forEach(function (r) { people[(r.name || "").toLowerCase() + "|" + r.dob] = 1; });
    if (sameDob.length > 1) {
      await final("NEEDS_REVIEW", sameDob.length + " Plan Year " + job.plan_year + " applications match" +
        (Object.keys(people).length > 1 ? " (" + Object.keys(people).length + " different people)" : "") +
        " — pick the right one yourself in HealthSherpa.");
    }
    var r = sameDob[0];
    if (r.appId) await save({ appId: r.appId });
    if (r.action === "view") {
      await final("NEEDS_REVIEW", "Already in your HealthSherpa clients (“View in Dash”). Open it there to check the authorization or resend it.", { hs_app_id: r.appId });
    }
    await step("add", "Adding the " + job.plan_year + " application" + (r.appId ? " (ID " + r.appId + ")" : ""), { hs_app_id: r.appId });
    await save({ phase: "adding" });
    await click(r.button);
    await handleAdd();
  }

  async function handleAdd() {
    var seen = await waitFor(function () {
      if (authModal()) return "modal";
      if (isProfilePage()) return "profile";
      return null;
    }, 30000, 500);
    if (!seen) await final("FAILED", "Clicked “Add to clients” but HealthSherpa didn't open the authorization window.");

    if (seen === "profile" && !authModal()) {
      await sleep(1500);
      if (!authModal()) {
        if (pageHas(/pending authorization|authorization required|get authorization/i)) {
          await final("NEEDS_REVIEW", "The client's profile opened but still needs authorization — use “Resend authorization” there.");
        }
        await final("AUTHORIZED", "HealthSherpa opened the client without asking — already authorized for " + job.plan_year + ".");
      }
    }

    var modal = authModal();
    if (pageHas(/authorization complete/i, modal)) { await doFinish(); return; }
    if (findButton(/^refresh status$/i, modal)) {
      // A link was already sent for this client (e.g. a retry) — just wait.
      await save({ phase: "link_sent", linkSends: Math.max(1, S.linkSends || 0) });
      await step("link", "A link was already sent — waiting for the client", { status: "LINK_SENT" });
      await waitForClient();
      return;
    }

    await step("popup", "Authorization window open");
    var textBtn = await waitFor(function () { return findButton(/^text link to/i, authModal() || document); }, 8000);
    if (!textBtn) {
      await final("NEEDS_REVIEW", "HealthSherpa has no mobile number for this client. Use “Change contact info” or “Email link” yourself.");
    }
    var hsPhone = last10(text(textBtn));
    if (job.phone && hsPhone && hsPhone.length === 10 && hsPhone !== job.phone) {
      await final("NEEDS_REVIEW", "HealthSherpa would text " + fmtPhone(hsPhone) + " but your sheet has " + fmtPhone(job.phone) +
        ". Confirm the number with the client — use “Change contact info” if it's wrong.");
    }

    var consent = checkboxByText(/include and save consumer consent/i, authModal() || document);
    if (consent) {
      await step("consent", "Ticking “Include and save Consumer Consent”");
      if (!(await ensureChecked(consent))) await final("FAILED", "Couldn't tick “Include and save Consumer Consent”.");
    }

    await save({ phase: "link_sent", linkSends: 1 });
    await click(textBtn);
    var waiting = await waitFor(function () {
      var m = authModal();
      return m && (findButton(/^refresh status$/i, m) || pageHas(/waiting for|text sent to/i, m) || pageHas(/authorization complete/i, m));
    }, 20000, 500);
    if (!waiting) await final("FAILED", "Clicked “Text link” but HealthSherpa didn't confirm the text was sent.");
    await step("link", "Text sent to " + fmtPhone(hsPhone) + " — waiting for the client to authorize",
      { status: "LINK_SENT", link_sent: true });
    await waitForClient();
  }

  async function waitForClient() {
    var lastRefresh = Date.now();
    var missingSince = 0;
    var maxSends = (job.max_resends || 0) + 1;
    while (true) {
      checkStop();
      var m = authModal();
      if (!m) {
        if (isProfilePage() && !pageHas(/pending authorization/i)) { await final("AUTHORIZED", "Authorized for " + job.plan_year + "."); }
        if (!missingSince) missingSince = Date.now();
        if (Date.now() - missingSince > 10000) {
          await final("NEEDS_REVIEW", "The authorization window was closed before the client finished. Check the client in HealthSherpa.");
        }
        await sleep(1000);
        continue;
      }
      missingSince = 0;

      if (pageHas(/authorization complete/i, m) || findButton(/^finish$/i, m)) { await doFinish(); return; }

      var t = text(m);
      var mm = t.match(/expire[sd]? in\s*(\d+):(\d{2})/i);
      var left = mm ? (+mm[1]) * 60 + (+mm[2]) : null;
      if (left !== null) overlay.timer(mm[1] + ":" + mm[2]);
      var expired = left === 0 || /\bexpired\b/i.test(t);

      if (expired) {
        if ((S.linkSends || 1) < maxSends) {
          var resend = findButton(/^resend by text$/i, m);
          if (!resend) await final("EXPIRED", "The link expired and there's no “Resend by text” button.");
          await save({ linkSends: (S.linkSends || 1) + 1 });
          await click(resend);
          await sleep(3000);
          lastStep = null;
          await step("resend", "Link expired — sent a new one (" + S.linkSends + " of " + maxSends + ")", { link_sent: true });
          await step("link", "Waiting for the client to authorize");
          lastRefresh = Date.now();
          continue;
        }
        await final("EXPIRED", "The client didn't authorize in time (link sent " + (S.linkSends || 1) + "×). Press Retry when they're ready.");
      }

      // HealthSherpa updates by itself; Refresh status is a safety net if it timed out.
      if (Date.now() - lastRefresh > 60000) {
        var rb = findButton(/^refresh status$/i, m);
        if (rb) { overlay.update("Checking status…"); rb.click(); }
        lastRefresh = Date.now();
        await sleep(1500);
        overlay.update("Waiting for the client to authorize");
      }
      await sleep(1500);
    }
  }

  async function doFinish() {
    await step("finish", "Client authorized — clicking Finish");
    await save({ phase: "finishing" });
    var fb = await waitFor(function () { return findButton(/^finish$/i, authModal() || document); }, 8000);
    if (fb) {
      await click(fb);
      await waitFor(function () { return isProfilePage() || !authModal(); }, 30000, 500);
    }
    await final("AUTHORIZED", "Authorized for " + job.plan_year + ".");
  }

  // ------------------------------------------------------------ start
  async function main() {
    // Remember the agent's HealthSherpa id from any page that shows it, so the
    // next job opens the search page directly.
    var aid = agentIdHere();
    if (aid) send({ kind: "hs-agent-id", id: aid });
    // Ask a few times: right after Start the background may still be saving.
    var res = null;
    for (var i = 0; i < 6 && !(res && res.state); i++) {
      if (i) await sleep(500);
      res = await send({ kind: "get-job" });
    }
    if (!res || !res.state) return;
    S = res.state;
    job = S.job;
    overlay.update("Starting…");
    try {
      await run();
    } catch (e) {
      if (e === STOPPED || e === DONE) return;
      var msg = (e && e.message) || String(e);
      try { await final("FAILED", "Unexpected problem on " + location.pathname + ": " + msg); } catch (x) { /* DONE */ }
    }
  }

  if (document.readyState === "complete") main();
  else window.addEventListener("load", main);
})();

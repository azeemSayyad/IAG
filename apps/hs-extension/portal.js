/* Runs on IAG portal pages. A thin, two-way bridge:
 *   portal page --window.postMessage--> here --runtime message--> background
 *   background --tabs message--> here --window.postMessage--> portal page
 * It accepts nothing from other frames or other origins.
 */
(function () {
  if (window.top !== window) return;
  if (!IAG.isPortalOrigin(location.origin)) return;

  var root = document.documentElement;
  if (root) root.dataset.iagHsExt = IAG.VERSION;

  function toPage(type, payload) {
    var msg = Object.assign({ source: IAG.EXT_SOURCE, type: type, version: IAG.VERSION }, payload || {});
    window.postMessage(msg, location.origin);
  }

  function announce() { toPage("ready"); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", announce);
  else announce();

  window.addEventListener("message", function (e) {
    if (e.source !== window || e.origin !== location.origin) return;
    var d = e.data;
    if (!d || d.source !== IAG.PAGE_SOURCE) return;

    if (d.type === "ping") { toPage("pong"); return; }

    if (d.type === "start-job") {
      chrome.runtime.sendMessage({ kind: "start-job", job: d.job, apiBase: d.apiBase }, function (res) {
        if (chrome.runtime.lastError || !res) {
          toPage("job-rejected", { id: d.job && d.job.id, reason: "The extension isn't responding — reload it in chrome://extensions." });
          return;
        }
        toPage(res.ok ? "job-accepted" : "job-rejected", { id: d.job && d.job.id, reason: res.reason });
      });
      return;
    }
    if (d.type === "stop-job") { chrome.runtime.sendMessage({ kind: "stop-job", id: d.id, reason: "Stopped from the portal." }); return; }
    if (d.type === "focus-tab") { chrome.runtime.sendMessage({ kind: "focus-tab" }); return; }
  });

  // Live step updates from the background -> the page.
  chrome.runtime.onMessage.addListener(function (msg) {
    if (msg && msg.kind === "job-update") toPage("job-update", msg.update);
  });
})();

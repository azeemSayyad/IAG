var PHASE = {
  nav: "Opening the search", searched: "Searching the Marketplace", adding: "Adding the 2027 application",
  link_sent: "Waiting for the client to authorize", finishing: "Finishing",
};

function renderAuto(a) {
  var box = document.getElementById("auto");
  box.innerHTML = "";
  if (!a || !a.on) { box.className = "auto off"; box.textContent = "Auto mode: off"; return; }
  box.className = "auto on";
  var t = document.createElement("span");
  t.textContent = "Auto mode: ON — the portal's HealthSherpa page hands clients over by itself.";
  var b = document.createElement("button");
  b.textContent = "Turn off";
  b.onclick = function () { chrome.runtime.sendMessage({ kind: "auto-off" }, refresh); };
  box.appendChild(t); box.appendChild(b);
}

function render(res) {
  renderAuto(res && res.auto);
  var el = document.getElementById("state");
  document.getElementById("ver").textContent = res && res.version ? "v" + res.version : "";
  document.getElementById("log").textContent = (res && res.log || []).slice().reverse().join("\n") || "Nothing yet.";
  var s = res && res.state;
  if (!s || s.stopped) { el.className = "idle"; el.textContent = "Idle — no client running."; return; }
  el.className = "";
  el.innerHTML = "";
  var name = document.createElement("div"); name.className = "job";
  name.textContent = (s.job.auto ? "Auto · " : "") + s.job.first_name + " " + s.job.last_name;
  var phase = document.createElement("div"); phase.className = "phase";
  phase.textContent = PHASE[s.phase] || s.phase;
  var stop = document.createElement("button"); stop.textContent = "Stop";
  stop.onclick = function () {
    chrome.runtime.sendMessage({ kind: "stop-job", id: s.job.id, reason: "Stopped from the extension.", fromExtension: true }, refresh);
  };
  el.appendChild(name); el.appendChild(phase); el.appendChild(stop);
}

function refresh() { chrome.runtime.sendMessage({ kind: "status" }, render); }
refresh();
setInterval(refresh, 2000);

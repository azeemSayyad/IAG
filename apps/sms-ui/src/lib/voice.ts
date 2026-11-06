import { api } from "./api";
import { playRemote, watchRemote } from "./remoteSound";

/* The agent's auto-dialer headset line (Telnyx WebRTC).

   While an agent is Available, the browser holds ONE open call that sits in
   the agent's private room. When the dialer reaches a live person it
   transfers that customer into the room — an instant bridge, no ring — which
   is how the agent can be talking within 2 seconds of the customer's "hello".

   A browser can't start a scripted call, so it ASKS the server to ring it
   (POST /voice/dialer/line, POST /voice/manual/calls) and answers that call by
   itself. Calls nobody here asked for are refused.

   The SDK is imported lazily so pages that never open a line don't pay for it.
   In the simulator (no keys, dev only) no SDK is loaded at all; the backend
   just marks the line open. */

export type LineState = "off" | "connecting" | "ready" | "mic_blocked" | "error";

/** The browser phone. Call ids are the SDK's own. */
type Phone = {
  /** Open the headset line. */
  line(): Promise<string>;
  /** Place a manual call: `create` logs it on the server (which also
   *  rings us); resolves once our leg is up. */
  manual(create: () => Promise<{ id: string }>): Promise<string>;
  /** Listen in on a call: `start` asks the server, which rings us. */
  monitor(start: () => Promise<unknown>): Promise<string>;
  hangup(id: string): Promise<void>;
  mute(id: string, muted: boolean): Promise<void>;
  dtmf(id: string, digits: string): Promise<void>;
};

type TelnyxCall = {
  id: string;
  state: string;
  direction: string;
  answer(): Promise<void>;
  hangup(): Promise<void>;
  muteAudio(): void;
  unmuteAudio(): void;
  dtmf(digits: string): void;
};

const RING_WAIT_MS = 20000;

async function telnyxPhone(token: string): Promise<Phone> {
  const { TelnyxRTC } = await import("@telnyx/webrtc");
  const c = new TelnyxRTC({ login_token: token });
  let audio = document.getElementById("telnyx-remote-audio") as HTMLAudioElement | null;
  if (!audio) {
    audio = Object.assign(document.createElement("audio"), { id: "telnyx-remote-audio", autoplay: true });
    audio.style.display = "none";
    document.body.appendChild(audio);
  }
  c.remoteElement = audio;
  watchRemote(audio);
  const calls = new Map<string, TelnyxCall>();
  let waiting: ((id: string) => void)[] = [];

  c.on("telnyx.notification", ((n: { type?: string; call?: TelnyxCall }) => {
    const call = n.call;
    if (n.type !== "callUpdate" || !call) return;
    if (call.state === "active") playRemote();
    if (call.state === "ringing" && call.direction === "inbound" && !calls.has(call.id)) {
      const take = waiting.shift();
      if (!take) {
        call.hangup().catch(() => undefined); // nobody here asked for this call
        return;
      }
      calls.set(call.id, call);
      call.answer().then(() => take(call.id)).catch(() => undefined);
    } else if ((call.state === "hangup" || call.state === "destroy") && calls.delete(call.id)) {
      callEnded(call.id);
    }
  }) as never);
  c.on("telnyx.socket.close", (() => sessionLost()) as never);

  await new Promise<void>((resolve, reject) => {
    const t = window.setTimeout(() => reject(new Error("Telnyx did not connect")), 15000);
    c.on("telnyx.ready", (() => {
      window.clearTimeout(t);
      resolve();
    }) as never);
    c.on("telnyx.error", (() => {
      window.clearTimeout(t);
      reject(new Error("Telnyx login failed"));
    }) as never);
    c.connect();
  });

  /** Wait for the server to ring this browser (register BEFORE asking). */
  function expectRing(): { ringing: Promise<string>; cancel: () => void } {
    let take!: (id: string) => void;
    let timer = 0;
    const ringing = new Promise<string>((resolve, reject) => {
      take = (id) => {
        window.clearTimeout(timer);
        resolve(id);
      };
      timer = window.setTimeout(() => {
        waiting = waiting.filter((w) => w !== take);
        reject(new Error("The call did not reach this browser"));
      }, RING_WAIT_MS);
    });
    waiting.push(take);
    return {
      ringing,
      cancel: () => {
        window.clearTimeout(timer);
        waiting = waiting.filter((w) => w !== take);
      },
    };
  }

  async function rungBy(ask: () => Promise<unknown>): Promise<string> {
    const r = expectRing();
    try {
      await ask();
    } catch (e) {
      r.cancel();
      throw e;
    }
    return r.ringing;
  }

  return {
    line: () => rungBy(() => api("/voice/dialer/line", { method: "POST", body: JSON.stringify({ on: true }) })),
    manual: (create) => rungBy(create),
    monitor: (start) => rungBy(start),
    async hangup(id) {
      await calls.get(id)?.hangup();
    },
    async mute(id, muted) {
      const call = calls.get(id);
      if (muted) call?.muteAudio();
      else call?.unmuteAudio();
    },
    async dtmf(id, digits) {
      calls.get(id)?.dtmf(digits);
    },
  };
}

let phone: Phone | null = null;
let phoneLoading: Promise<Phone> | null = null; // one SDK session serves the headset line AND manual calls
let callId: string | null = null;
let simulated = false;
let opening: Promise<void> | null = null;
let listeners: ((s: LineState, detail?: string) => void)[] = [];
let state: LineState = "off";

function set(next: LineState, detail?: string) {
  state = next;
  listeners.forEach((l) => l(next, detail));
}

export function onLineState(cb: (s: LineState, detail?: string) => void): () => void {
  listeners.push(cb);
  cb(state);
  return () => {
    listeners = listeners.filter((l) => l !== cb);
  };
}

export function lineState(): LineState {
  return state;
}

function callEnded(id: string) {
  if (id === callId) {
    callId = null;
    set("off", "line dropped");
  } else if (id === manualSdkId) {
    manualSdkId = null;
    manualDropped();
  } else if (id === monitorSdkId) {
    monitorSdkId = null;
    monitorDropped();
  }
}

function sessionLost() {
  phone = null;
  callId = null;
  set("error", "session lost");
  if (manualSdkId) {
    manualSdkId = null;
    manualDropped();
  }
  if (monitorSdkId) {
    monitorSdkId = null;
    monitorDropped();
  }
}

/** The signed-in browser phone (created on first use). */
function sdk(): Promise<Phone> {
  if (phone) return Promise.resolve(phone);
  if (!phoneLoading) {
    phoneLoading = (async () => {
      const { token } = await api<{ token: string }>("/voice/dialer/token", { method: "POST" });
      phone = await telnyxPhone(token);
      return phone;
    })().finally(() => {
      phoneLoading = null;
    });
  }
  return phoneLoading;
}

async function micAllowed(): Promise<boolean> {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}

/** Open the line (idempotent). `simulate` = dev simulator, no real audio. */
export function openLine(simulate: boolean): Promise<void> {
  if (state === "ready" || opening) return opening || Promise.resolve();
  opening = (async () => {
    set("connecting");
    try {
      if (simulate) {
        simulated = true;
        await api("/voice/dialer/line", { method: "POST", body: JSON.stringify({ on: true }) });
        set("ready");
        return;
      }
      simulated = false;
      if (!(await micAllowed())) {
        set("mic_blocked");
        return;
      }
      const p = await sdk();
      callId = await p.line();
      set("ready");
    } catch (e) {
      callId = null;
      set("error", e instanceof Error ? e.message : String(e));
    } finally {
      opening = null;
    }
  })();
  return opening;
}

/** Close the line (agent left / on break / dialer off). */
export async function closeLine(): Promise<void> {
  const id = callId;
  callId = null;
  try {
    if (phone && id) await phone.hangup(id);
  } catch {
    /* already gone */
  }
  try {
    await api("/voice/dialer/line", { method: "POST", body: JSON.stringify({ on: false }) });
  } catch {
    /* best effort — the backend also notices the leg ending */
  }
  set("off");
  simulated = false;
}

export async function setMuted(muted: boolean): Promise<void> {
  if (!phone || !callId) return;
  await phone.mute(callId, muted);
}

export function isSimulated(): boolean {
  return simulated;
}

/* ===== Manual dialing (Dial drawer) =====

   Anyone can call any number. The server logs the call first (POST
   /voice/manual/calls — that also marks the caller busy, so no pool offer or
   dialer bridge reaches them, closes their headset line, and rings this
   browser, which answers by itself); Telnyx then records it and rings the
   number. The call's status is read back from the server
   (polled + socket "voice:manual"), so the drawer and the mini dock show the
   same thing wherever they are mounted. */

export type ManualCall = {
  id: string;
  phone_number: string;
  from_number: string | null;
  status: string; // DIALING | RINGING | ANSWERED | COMPLETED | NO_ANSWER | BUSY | FAILED | CANCELLED
  created_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
};
export type ManualState = { call: ManualCall | null; starting: boolean; error: string | null; simulated: boolean };

export const MANUAL_LIVE = ["DIALING", "RINGING", "ANSWERED"];
const SIM_ANSWER_MS = 2000;
const MANUAL_POLL_MS = 1500;

let manualSdkId: string | null = null;
let mState: ManualState = { call: null, starting: false, error: null, simulated: false };
let mListeners: ((s: ManualState) => void)[] = [];
let pollTimer: number | null = null;

function setManual(patch: Partial<ManualState>) {
  mState = { ...mState, ...patch };
  mListeners.forEach((l) => l(mState));
}

export function onManual(cb: (s: ManualState) => void): () => void {
  mListeners.push(cb);
  cb(mState);
  return () => {
    mListeners = mListeners.filter((l) => l !== cb);
  };
}

export function manualLive(): boolean {
  return mState.starting || (!!mState.call && MANUAL_LIVE.includes(mState.call.status));
}

async function pollManual() {
  const id = mState.call?.id;
  if (!id) return;
  try {
    const call = await api<ManualCall>(`/voice/manual/calls/${id}`);
    if (mState.call?.id !== id) return;
    setManual({ call });
    if (!MANUAL_LIVE.includes(call.status)) stopPoll(true);
  } catch {
    /* transient — next tick */
  }
}

function startPoll() {
  stopPoll(false);
  pollTimer = window.setInterval(pollManual, MANUAL_POLL_MS);
}

function stopPoll(ended: boolean) {
  if (pollTimer !== null) window.clearInterval(pollTimer);
  pollTimer = null;
  if (ended && manualSdkId && phone) {
    // The phone side is over (busy / no answer / they hung up): drop our leg too.
    const id = manualSdkId;
    manualSdkId = null;
    phone.hangup(id).catch(() => undefined);
  }
}

/** Socket nudge ("voice:manual"): re-read now instead of on the next tick. */
export function nudgeManual() {
  if (mState.call && MANUAL_LIVE.includes(mState.call.status)) pollManual();
}

function manualDropped() {
  // The browser's call ended on its own (network, carrier) — end it server-side.
  const id = mState.call?.id;
  if (!id || !mState.call || !MANUAL_LIVE.includes(mState.call.status)) return;
  api<ManualCall>(`/voice/manual/calls/${id}/end`, { method: "POST" })
    .then((call) => setManual({ call }))
    .catch(() => pollManual())
    .finally(() => stopPoll(false));
}

/** api() errors read "409 Conflict: {\"detail\": …}" — show just the detail. */
function errorText(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  const m = msg.match(/\{.*\}$/s);
  if (m) {
    try {
      const d = JSON.parse(m[0])?.detail;
      if (typeof d === "string" && d) return d;
    } catch {
      /* not JSON */
    }
  }
  return msg;
}

export async function startManualCall(to: string, from: string | null): Promise<void> {
  if (manualLive()) return;
  setManual({ starting: true, error: null, call: null });
  let call: ManualCall | null = null;
  try {
    const simulate = (await api<{ simulate: boolean }>("/voice/manual/config")).simulate;
    if (!simulate && !(await micAllowed())) {
      setManual({ starting: false, error: "Microphone blocked — allow it in the browser to call" });
      return;
    }
    const create = async () => {
      call = await api<ManualCall>("/voice/manual/calls", {
        method: "POST",
        body: JSON.stringify({ to, from_number: from }),
      });
      setManual({ call, simulated: simulate });
      startPoll();
      return call;
    };
    if (simulate) {
      // Simulator: the person picks up after a moment.
      const id = (await create()).id;
      window.setTimeout(() => {
        if (mState.call?.id === id && MANUAL_LIVE.includes(mState.call.status)) {
          api<ManualCall>(`/voice/manual/calls/${id}/sim-answer`, { method: "POST" })
            .then((c) => mState.call?.id === id && setManual({ call: c }))
            .catch(() => undefined);
        }
      }, SIM_ANSWER_MS);
    } else {
      const p = await sdk();
      manualSdkId = await p.manual(create);
    }
    setManual({ starting: false });
  } catch (e) {
    setManual({ starting: false, error: errorText(e) });
    if (call) await endManualCall();
  }
}

export async function endManualCall(): Promise<void> {
  const id = mState.call?.id;
  const sdkId = manualSdkId;
  manualSdkId = null;
  if (phone && sdkId) await phone.hangup(sdkId).catch(() => undefined);
  if (!id) return;
  try {
    setManual({ call: await api<ManualCall>(`/voice/manual/calls/${id}/end`, { method: "POST" }) });
  } catch {
    await pollManual();
  }
  stopPoll(false);
}

/** Clear a finished call off the drawer (ready for the next number). */
export function resetManual() {
  if (manualLive()) return;
  setManual({ call: null, error: null });
}

export async function muteManual(muted: boolean): Promise<void> {
  if (!phone || !manualSdkId) return;
  await phone.mute(manualSdkId, muted);
}

/** Keypad during a live call (menus: "press 1 for…"). */
export async function sendManualDigits(digits: string): Promise<void> {
  if (!phone || !manualSdkId) return;
  await phone.dtmf(manualSdkId, digits).catch(() => undefined);
}

/* ===== Listen in / whisper (managers, Auto Dialer → Live calls) =====

   The server rings this browser into the call's room MUTED (POST
   /voice/dialer/monitor); it answers by itself, like a manual call. Whisper
   is switched server-side (only the agent hears you). As a second lock the
   browser also mutes its own microphone whenever the mode is "listen", so
   nothing is sent even if the carrier side went wrong. The session ends when
   the call does (the server hangs us up) or on Leave. */

export type MonitorMode = "listen" | "whisper";
export type Monitor = {
  id: string;
  user_id: string;
  attempt_id: string;
  mode: MonitorMode;
  status: string; // RINGING | LIVE | ENDED
  agent_name: string | null;
  customer_phone: string | null;
  call_status: string | null;
  simulated: boolean;
};
/** target = the call last acted on (where an error is shown). */
export type MonitorState = { monitor: Monitor | null; starting: string | null; target: string | null; switching: boolean; error: string | null };

const MONITOR_POLL_MS = 2000;
let monitorSdkId: string | null = null;
let vState: MonitorState = { monitor: null, starting: null, target: null, switching: false, error: null };
let vListeners: ((s: MonitorState) => void)[] = [];
let vTimer: number | null = null;

function setMon(patch: Partial<MonitorState>) {
  vState = { ...vState, ...patch };
  vListeners.forEach((l) => l(vState));
}

export function onMonitor(cb: (s: MonitorState) => void): () => void {
  vListeners.push(cb);
  cb(vState);
  return () => {
    vListeners = vListeners.filter((l) => l !== cb);
  };
}

function monitorGone() {
  if (vTimer !== null) window.clearInterval(vTimer);
  vTimer = null;
  const id = monitorSdkId;
  monitorSdkId = null;
  if (phone && id) phone.hangup(id).catch(() => undefined);
  setMon({ monitor: null, switching: false });
}

async function pollMonitor() {
  try {
    const { monitor } = await api<{ monitor: Monitor | null }>("/voice/dialer/monitor");
    if (!monitor) monitorGone();
    else setMon({ monitor });
  } catch {
    /* transient — next tick */
  }
}

function startMonitorPoll() {
  if (vTimer !== null) window.clearInterval(vTimer);
  vTimer = window.setInterval(pollMonitor, MONITOR_POLL_MS);
}

function monitorDropped() {
  // Our leg ended on its own (network, tab) — close the session server-side.
  const id = vState.monitor?.id;
  if (id) api(`/voice/dialer/monitor/${id}/end`, { method: "POST" }).catch(() => undefined);
  monitorGone();
}

/** Socket nudge ("voice:monitor"). */
export function nudgeMonitor() {
  if (vState.monitor) pollMonitor();
}

export async function startMonitor(attemptId: string): Promise<void> {
  if (vState.starting) return;
  if (vState.monitor) await endMonitor();
  setMon({ starting: attemptId, target: attemptId, error: null });
  try {
    const simulate = (await api<{ simulate: boolean }>("/voice/manual/config")).simulate;
    let monitor: Monitor | null = null;
    const ask = async () => {
      monitor = await api<Monitor>("/voice/dialer/monitor", {
        method: "POST",
        body: JSON.stringify({ attempt_id: attemptId }),
      });
      setMon({ monitor });
      return monitor;
    };
    if (simulate) {
      await ask();
    } else {
      if (!(await micAllowed())) {
        setMon({ starting: null, error: "Microphone blocked — allow it in the browser to listen in" });
        return;
      }
      const p = await sdk();
      monitorSdkId = await p.monitor(ask);
      await p.mute(monitorSdkId, true); // listen = our mic sends nothing
    }
    startMonitorPoll();
    await pollMonitor();
    setMon({ starting: null });
  } catch (e) {
    setMon({ starting: null, error: errorText(e) });
    if (vState.monitor) await endMonitor();
  }
}

export async function setMonitorMode(mode: MonitorMode): Promise<void> {
  const m = vState.monitor;
  if (!m || vState.switching || m.mode === mode) return;
  setMon({ switching: true, error: null });
  try {
    if (mode === "listen" && phone && monitorSdkId) await phone.mute(monitorSdkId, true);
    const next = await api<Monitor>(`/voice/dialer/monitor/${m.id}/mode`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
    // Unmute only after the server confirmed only the agent can hear us.
    if (next.mode === "whisper" && phone && monitorSdkId) await phone.mute(monitorSdkId, false);
    setMon({ monitor: next, switching: false });
  } catch (e) {
    if (phone && monitorSdkId) await phone.mute(monitorSdkId, true).catch(() => undefined);
    setMon({ switching: false, error: errorText(e) });
    await pollMonitor();
  }
}

export async function endMonitor(): Promise<void> {
  const id = vState.monitor?.id;
  monitorGone();
  if (id) await api(`/voice/dialer/monitor/${id}/end`, { method: "POST" }).catch(() => undefined);
}

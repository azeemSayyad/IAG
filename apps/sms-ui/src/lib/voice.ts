import { api } from "./api";

/* The agent's auto-dialer headset line (Vonage Client SDK, WebRTC).

   While an agent is Available, the browser holds ONE open call to Vonage that
   sits in the agent's private room. When the dialer reaches a live person it
   transfers that customer into the room — an instant bridge, no ring — which
   is how the agent can be talking within 2 seconds of the customer's "hello".

   The SDK is imported lazily so pages that never open a line don't pay for it.
   In the simulator (no Vonage keys, dev only) no SDK is loaded at all; the
   backend just marks the line open. */

export type LineState = "off" | "connecting" | "ready" | "mic_blocked" | "error";

type Client = {
  createSession(token: string): Promise<string>;
  deleteSession(): Promise<unknown>;
  serverCall(context?: Record<string, unknown>): Promise<string>;
  hangup(callId: string): Promise<void>;
  mute(callId: string): Promise<unknown>;
  unmute(callId: string): Promise<unknown>;
  sendDTMF(callId: string, digits: string): Promise<void>;
  on(event: string, cb: (...args: never[]) => void): symbol;
};

let client: Client | null = null;
let session = false; // one SDK session serves the headset line AND manual calls
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

/** The logged-in SDK client (created + signed in on first use). */
async function sdk(): Promise<Client> {
  if (!client) {
    const mod = await import("@vonage/client-sdk");
    // Same datacenter as the Vonage application (North America - East).
    client = new mod.VonageClient({ region: "US" } as never) as unknown as Client;
    client.on("callHangup", ((id: string) => {
      if (id === callId) {
        callId = null;
        set("off", "line dropped");
      } else if (id === manualSdkId) {
        manualSdkId = null;
        manualDropped();
      }
    }) as never);
    client.on("sessionError", (() => {
      session = false;
      callId = null;
      set("error", "session lost");
      if (manualSdkId) {
        manualSdkId = null;
        manualDropped();
      }
    }) as never);
  }
  if (!session) {
    const { token } = await api<{ token: string }>("/voice/dialer/token", { method: "POST" });
    await client.createSession(token);
    session = true;
  }
  return client;
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
      const c = await sdk();
      callId = await c.serverCall({ purpose: "agent_line" });
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
    if (client && id) await client.hangup(id);
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
  if (!client || !callId) return;
  if (muted) await client.mute(callId);
  else await client.unmute(callId);
}

export function isSimulated(): boolean {
  return simulated;
}

/* ===== Manual dialing (Dial drawer) =====

   Anyone can call any number. The server logs the call first (POST
   /voice/manual/calls — that also marks the caller busy, so no pool offer or
   dialer bridge reaches them, and closes their headset line); then the
   browser places it with serverCall({purpose: "manual", mc}). Vonage records
   it and rings the number. The call's status is read back from the server
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
  if (ended && manualSdkId && client) {
    // The phone side is over (busy / no answer / they hung up): drop our leg too.
    const id = manualSdkId;
    manualSdkId = null;
    client.hangup(id).catch(() => undefined);
  }
}

/** Socket nudge ("voice:manual"): re-read now instead of on the next tick. */
export function nudgeManual() {
  if (mState.call && MANUAL_LIVE.includes(mState.call.status)) pollManual();
}

function manualDropped() {
  // The browser's call ended on its own (network, Vonage) — end it server-side.
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
    call = await api<ManualCall>("/voice/manual/calls", {
      method: "POST",
      body: JSON.stringify({ to, from_number: from }),
    });
    setManual({ call, simulated: simulate });
    startPoll();
    if (simulate) {
      // Simulator: the person picks up after a moment.
      const id = call.id;
      window.setTimeout(() => {
        if (mState.call?.id === id && MANUAL_LIVE.includes(mState.call.status)) {
          api<ManualCall>(`/voice/manual/calls/${id}/sim-answer`, { method: "POST" })
            .then((c) => mState.call?.id === id && setManual({ call: c }))
            .catch(() => undefined);
        }
      }, SIM_ANSWER_MS);
    } else {
      const c = await sdk();
      manualSdkId = await c.serverCall({ purpose: "manual", mc: call.id });
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
  if (client && sdkId) await client.hangup(sdkId).catch(() => undefined);
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
  if (!client || !manualSdkId) return;
  if (muted) await client.mute(manualSdkId);
  else await client.unmute(manualSdkId);
}

/** Keypad during a live call (menus: "press 1 for…"). */
export async function sendManualDigits(digits: string): Promise<void> {
  if (!client || !manualSdkId) return;
  await client.sendDTMF(manualSdkId, digits).catch(() => undefined);
}

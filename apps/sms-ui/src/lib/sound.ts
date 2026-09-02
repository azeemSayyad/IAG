/* Tiny WebAudio notification sounds — no asset files, no dependencies.
   Mirrors the portal's existing beep() (services/call-now-popup.js) so the SMS
   app stays consistent. Browsers block autoplay until a user gesture, so call
   unlockSound() from a click handler (e.g. "Join queue") to enable audio. */

const STORAGE_KEY = "sms_sound_muted";
let ctx: AudioContext | null = null;
let muted = false;

export function initSound(): void {
  try {
    muted = localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    /* ignore */
  }
}

export function isMuted(): boolean {
  return muted;
}

export function setMuted(m: boolean): void {
  muted = m;
  try {
    localStorage.setItem(STORAGE_KEY, m ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function getCtx(): AudioContext | null {
  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!Ctx) return null;
    if (!ctx) ctx = new Ctx();
    return ctx;
  } catch {
    return null;
  }
}

/** Resume the audio context after a user gesture (autoplay policy). */
export function unlockSound(): void {
  const c = getCtx();
  if (c && c.state === "suspended") c.resume().catch(() => {});
}

function beep(freq: number, durMs: number, volume: number): void {
  if (muted) return;
  const c = getCtx();
  if (!c) return;
  try {
    if (c.state === "suspended") c.resume().catch(() => {});
    const o = c.createOscillator();
    const g = c.createGain();
    o.type = "sine";
    o.frequency.value = freq;
    o.connect(g);
    g.connect(c.destination);
    const t = c.currentTime;
    const end = t + durMs / 1000;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(volume, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, end);
    o.start(t);
    o.stop(end + 0.02);
  } catch {
    /* ignore */
  }
}

/** Attention-grabbing two-tone chime for a newly offered lead. */
export function leadOfferedSound(): void {
  beep(880, 300, 0.3);
  setTimeout(() => beep(1175, 350, 0.3), 180);
}

/** Soft tick for a new inbound customer message. */
export function inboundTick(): void {
  beep(660, 120, 0.08);
}

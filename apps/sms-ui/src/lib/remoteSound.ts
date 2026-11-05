/* Chrome won't start audio on a page nobody has clicked yet, and the Telnyx SDK
   only sets `autoplay` and never retries. The headset line reopens by itself
   after a page load, so the agent's mic worked but the caller stayed silent.
   So: try to play whenever audio arrives, retry on the next click or key, and
   tell the page while it's blocked so it can ask for that click. */

let el: HTMLAudioElement | null = null;
let blocked = false;
let listeners: ((blocked: boolean) => void)[] = [];

function setBlocked(next: boolean) {
  if (next === blocked) return;
  blocked = next;
  listeners.forEach((l) => l(next));
}

export function onSoundBlocked(cb: (blocked: boolean) => void): () => void {
  listeners.push(cb);
  cb(blocked);
  return () => {
    listeners = listeners.filter((l) => l !== cb);
  };
}

export function playRemote() {
  if (!el || !el.srcObject) {
    setBlocked(false);
    return;
  }
  if (!el.paused) {
    setBlocked(false);
    return;
  }
  el.play()
    .then(() => setBlocked(false))
    .catch((e: unknown) => {
      if (e instanceof Error && e.name === "NotAllowedError") setBlocked(true);
    });
}

export function watchRemote(audio: HTMLAudioElement) {
  if (el === audio) return;
  if (!el) {
    document.addEventListener("pointerdown", playRemote, true);
    document.addEventListener("keydown", playRemote, true);
  }
  el = audio;
  audio.addEventListener("loadedmetadata", playRemote);
}

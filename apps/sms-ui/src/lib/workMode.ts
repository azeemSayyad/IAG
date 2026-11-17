import { api } from "./api";

/* How an agent works this session — ONE system at a time (owner, 2026-11-17):
     "sms"    = SMS leads: the pool offers leads in the popup; no dialer calls.
     "dialer" = Auto dialer: the browser phone line takes live calls; no pool popups.
   The server holds the truth (sms_queue_agents.voice_ready + status); this
   file only remembers that the agent CHOSE in this browser tab, so a fresh
   login always asks again. sessionStorage survives reloads and page changes in
   the same tab, and is gone in a new tab / after closing the browser. */

export type WorkMode = "sms" | "dialer";
const KEY = "iagWorkMode";

export function getWorkMode(): WorkMode | null {
  try {
    const v = sessionStorage.getItem(KEY);
    return v === "sms" || v === "dialer" ? v : null;
  } catch {
    return null;
  }
}

export function setWorkMode(m: WorkMode): void {
  try {
    sessionStorage.setItem(KEY, m);
  } catch {
    /* private mode etc. — the agent just gets asked again next load */
  }
}

let fresh: Promise<void> | null = null;

/** Run once per page load, before anything can show a lead popup. If the agent
 *  hasn't chosen a mode in this tab yet and the server still has them online
 *  from an earlier session (offered a lead the moment they log in — the popup
 *  then covered the dialer switch), take them offline so Lead Manager asks
 *  "SMS leads or Auto dialer?" first. Someone in the middle of a lead or a call
 *  is never interrupted: their mode is just remembered. */
export function freshStart(): Promise<void> {
  if (fresh) return fresh;
  fresh = (async () => {
    if (getWorkMode()) return;
    try {
      const [s, c, d] = await Promise.all([
        api<{ status: string }>("/sms/queue/status"),
        api<{ lead: { status: string } | null }>("/sms/queue/current"),
        api<{ agent: { ready: boolean } }>("/voice/dialer/state").catch(() => null),
      ]);
      const ready = !!d?.agent?.ready;
      if (c.lead?.status === "IN_PROGRESS" || s.status === "ON_CALL") {
        setWorkMode(ready ? "dialer" : "sms");
        return;
      }
      if (s.status && s.status !== "OFFLINE") {
        await api("/sms/queue/leave", { method: "POST" });
      }
    } catch {
      /* transient — worst case the old state shows; the agent can still switch */
    }
  })();
  return fresh;
}

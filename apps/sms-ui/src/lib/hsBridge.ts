/* Talks to the IAG HealthSherpa Chrome extension through window.postMessage.
 *
 * The extension's content script runs on portal pages and answers messages
 * tagged `source: "iag-portal"` with messages tagged `source: "iag-hs-ext"`.
 * The extension never sees the agent's portal login: Start hands it ONE job and
 * a token that only works for that job (see app/hs_auth/service.py).
 */
import { useEffect, useState } from "react";

export const EXT_SOURCE = "iag-hs-ext";
export const PAGE_SOURCE = "iag-portal";

export type ExtInfo = { version: string } | null;

export type ExtUpdate = {
  id: string;
  step?: string;
  status?: string;
  detail?: string;
  waiting_login?: boolean;
};

export type HsJob = {
  id: string;
  token: string;
  first_name: string;
  last_name: string;
  dob: string;
  dob_iso: string;
  state: string;
  state_name: string;
  phone: string | null;
  email: string | null;
  max_resends: number;
  plan_year: number;
};

function post(type: string, payload: Record<string, unknown> = {}) {
  window.postMessage({ source: PAGE_SOURCE, type, ...payload }, window.location.origin);
}

/** Whether the extension is installed (it answers a ping), re-checked every 5 s
 *  so installing it while the page is open is picked up without a reload. */
export function useExtension(): ExtInfo {
  const [info, setInfo] = useState<ExtInfo>(() => {
    const v = document.documentElement.dataset.iagHsExt;
    return v ? { version: v } : null;
  });
  useEffect(() => {
    let lastSeen = info ? Date.now() : 0;
    const onMsg = (e: MessageEvent) => {
      if (e.source !== window || e.data?.source !== EXT_SOURCE) return;
      if (e.data.type === "ready" || e.data.type === "pong") {
        lastSeen = Date.now();
        setInfo((cur) => (cur?.version === e.data.version ? cur : { version: String(e.data.version || "?") }));
      }
    };
    window.addEventListener("message", onMsg);
    post("ping");
    const t = window.setInterval(() => {
      post("ping");
      // No answer for 12 s (extension removed / disabled) -> show not installed.
      if (lastSeen && Date.now() - lastSeen > 12000) { lastSeen = 0; setInfo(null); }
    }, 5000);
    return () => { window.removeEventListener("message", onMsg); window.clearInterval(t); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return info;
}

/** Live step updates from the extension (the page also polls the server). */
export function useExtensionUpdates(onUpdate: (u: ExtUpdate) => void) {
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.source !== window || e.data?.source !== EXT_SOURCE || e.data.type !== "job-update") return;
      onUpdate(e.data as ExtUpdate);
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [onUpdate]);
}

/** Hand a started job to the extension. Resolves once it has accepted it. */
export function sendJob(job: HsJob): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      window.removeEventListener("message", onMsg);
      reject(new Error("The Chrome extension didn't answer. Make sure it's installed and turned on, then press Start again."));
    }, 6000);
    function onMsg(e: MessageEvent) {
      if (e.source !== window || e.data?.source !== EXT_SOURCE) return;
      if (e.data.type !== "job-accepted" && e.data.type !== "job-rejected") return;
      if (e.data.id !== job.id) return;
      window.clearTimeout(timer);
      window.removeEventListener("message", onMsg);
      if (e.data.type === "job-accepted") resolve();
      else reject(new Error(e.data.reason || "The extension refused the job."));
    }
    window.addEventListener("message", onMsg);
    post("start-job", { job, apiBase: `${window.location.origin}/api/v1` });
  });
}

export function stopJob(id: string) {
  post("stop-job", { id });
}

export function focusHealthSherpa() {
  post("focus-tab");
}

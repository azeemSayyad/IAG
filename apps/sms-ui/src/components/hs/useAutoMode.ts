/* AUTO MODE for HealthSherpa text campaigns.
 *
 * The campaign's HealthSherpa agent (Stan) leaves the HealthSherpa page open in
 * the Chrome where they're logged in to HealthSherpa, with Auto mode on. This
 * hook asks the server for the next ANSWERED client every few seconds
 * (POST /hs-auth/auto/next) and hands it to the extension exactly like Start —
 * one job, one one-job token. The SERVER still enforces the daily limit, one
 * client at a time, and client texting hours; it also ends a job at the link
 * wait (15 min), and this hook then tells the extension to stop it.
 *
 * Chrome slows timers in a hidden tab to about once a minute, so a job ending
 * (the extension's "job-update" message — events are never throttled) also
 * asks for the next one at once.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import {
  sendJob, setAutoMode, stopJob, useAutoOffRequest, useExtensionUpdates,
  type ExtInfo, type ExtUpdate, type HsJob,
} from "../../lib/hsBridge";
import { errText } from "./ui";

const KEY = "hsAutoMode";
const POLL_MS = 5000;
const FINAL = new Set(["AUTHORIZED", "NOT_FOUND", "NEEDS_REVIEW", "EXPIRED", "FAILED", "CANCELLED", "INVALID"]);

export type AutoInfo = { reason: string; waiting: number; name?: string } | null;
type NextResp = { job: HsJob | null; reason?: string; waiting?: number; running_id?: string };

function readOn(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}
function writeOn(on: boolean) {
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* private window */ }
}

export function useAutoMode(ext: ExtInfo, allowed: boolean, onChange: () => void) {
  const [on, setOn] = useState(readOn);
  const [info, setInfo] = useState<AutoInfo>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const current = useRef<string | null>(null);
  const active = on && allowed && !!ext;

  const toggle = useCallback((v: boolean) => {
    setOn(v); writeOn(v); setAutoMode(v);
    if (!v) setInfo(null);
    if (v) setError(null);
  }, []);

  useEffect(() => { setAutoMode(active); }, [active]);

  const poll = useCallback(async () => {
    if (!active || busy.current) return;
    busy.current = true;
    try {
      const r = await api<NextResp>("/hs-auth/auto/next", { method: "POST" });
      if (r.job) {
        // The server already ended the previous one (e.g. 15-min expiry) — make
        // sure the extension stops it before taking the next.
        if (current.current && current.current !== r.job.id) {
          stopJob(current.current);
          await new Promise((res) => setTimeout(res, 600));
        }
        try {
          await sendJob(r.job);
          current.current = r.job.id;
          setInfo({ reason: "started", waiting: r.waiting || 0, name: `${r.job.first_name} ${r.job.last_name}` });
          setError(null);
        } catch (e) {
          await api(`/hs-auth/auto/${r.job.id}/release`, { method: "POST" }).catch(() => {});
          setError(`Auto mode stopped: ${errText(e)}`);
          toggle(false);
        }
        onChange();
      } else {
        if (r.reason === "running") {
          current.current = r.running_id || current.current;
        } else if (current.current) {
          stopJob(current.current); // harmless if it already finished
          current.current = null;
          onChange();
        }
        setInfo({ reason: r.reason || "idle", waiting: r.waiting || 0 });
      }
    } catch (e) {
      setError(errText(e));
    } finally {
      busy.current = false;
    }
  }, [active, onChange, toggle]);

  useEffect(() => {
    if (!active) return;
    poll();
    const t = window.setInterval(poll, POLL_MS);
    return () => window.clearInterval(t);
  }, [active, poll]);

  const onUpdate = useCallback((u: ExtUpdate) => {
    if (u.status && FINAL.has(u.status)) window.setTimeout(poll, 1500);
  }, [poll]);
  useExtensionUpdates(onUpdate);
  useAutoOffRequest(useCallback(() => toggle(false), [toggle]));

  return { on, active, toggle, info, error, clearError: () => setError(null) };
}

export function autoLabel(info: AutoInfo, usageLeft: number | undefined): string {
  if (!info) return "Starting…";
  switch (info.reason) {
    case "started": return `Running ${info.name || "a client"}${info.waiting ? ` · ${info.waiting} more waiting` : ""}`;
    case "running": return `Working on a client${info.waiting ? ` · ${info.waiting} waiting` : ""}`;
    case "limit": return `Daily limit reached — ${info.waiting} waiting until tomorrow`;
    case "hours": return `${info.waiting} waiting for their texting hours (8 am – 8 pm their time)`;
    default: return usageLeft === 0 ? "Daily limit reached" : "Waiting for clients to answer";
  }
}

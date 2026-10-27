import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { isAdmin } from "../lib/auth";
import { formatPhone } from "../lib/phone";
import { getSocket } from "../lib/socket";
import { leadOfferedSound } from "../lib/sound";
import { closeLine, onLineState, openLine, setMuted, type LineState } from "../lib/voice";
import { DetailGrid, type DetailField } from "./LeadDetails";

/* YES-reply auto dialer — the agent's side. Mounted once in PortalShell.

   • Keeps the agent's headset line open only while they have "Ready for calls"
     on (Lead Manager) and are Available, or on an auto-dialed call. Working a
     regular pool lead, on break or after Leave, the line is closed — an open
     line is billed by Vonage per minute.
   • Pops the lead card the moment a live customer is bridged onto that line
     ("voice:connected"). No Accept/Pass — the agent is already talking.
   • A small dock shows the line state, a call timer, Mute and Hang up.

   Driven by GET /voice/dialer/state (polled + nudged by socket events), so a
   missed socket event can only delay the card by one poll. */

type CallInfo = {
  id: string;
  status: string; // CONNECTED | COMPLETED
  connected_at: string | null;
  direction: string;
};
type Card = {
  id: string;
  customer_name: string | null;
  phone_number: string;
  address: string | null;
  state: string | null;
  dob: string | null;
  yes_reply: string | null;
  list_name: string | null;
  notes: string | null;
  details?: DetailField[];
  call: CallInfo;
};
type DialerState = {
  enabled: boolean;
  available: boolean;
  simulate: boolean;
  agent: { status: string; ready: boolean; line: boolean };
  call: Card | null;
  /** On a call they dialed by hand (Dial drawer): busy, line stays closed. */
  manual?: boolean;
};

// The socket delivers the pop instantly; this poll is the fallback for when it
// is down. With a line open it runs fast enough to keep the spec's 1-2 s target.
const POLL_MS = 3000;
const POLL_LINE_MS = 1500;
const SHOWN_KEY = "voice_popped_call";

function initials(name: string | null): string {
  if (!name) return "•";
  const p = name.trim().split(/\s+/);
  return ((p[0]?.[0] || "") + (p.length > 1 ? p[p.length - 1][0] || "" : "")).toUpperCase() || "•";
}

function clock(iso: string | null): string {
  if (!iso) return "0:00";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const LINE_LABEL: Record<LineState, string> = {
  off: "Line off",
  connecting: "Connecting headset line…",
  ready: "Headset line ready — waiting for calls",
  mic_blocked: "Microphone blocked — allow it in the browser to take calls",
  error: "Line problem — retrying",
};

export default function AgentLine() {
  const admin = isAdmin();
  const [ds, setDs] = useState<DialerState | null>(null);
  const [line, setLine] = useState<LineState>("off");
  const [pop, setPop] = useState<Card | null>(null);
  // Connect-on-answer: a call can turn out to be voicemail after the card
  // popped. The server then frees the agent; this closes the card and says why.
  const [dropped, setDropped] = useState<string | null>(null);
  const [muted, setMutedState] = useState(false);
  const [busy, setBusy] = useState(false);
  const [, tick] = useState(0);
  const retryAt = useRef(0);

  const refresh = useCallback(async () => {
    if (admin) return;
    try {
      setDs(await api<DialerState>("/voice/dialer/state"));
    } catch {
      /* transient */
    }
  }, [admin]);

  useEffect(() => onLineState((s) => setLine(s)), []);

  const fastPoll = line === "ready";
  useEffect(() => {
    if (admin) return;
    refresh();
    const id = setInterval(refresh, fastPoll ? POLL_LINE_MS : POLL_MS);
    const s = getSocket();
    const nudge = () => refresh();
    const onConnected = (card: Card) => {
      showPop(card);
      refresh();
    };
    const evts = ["voice:line", "voice:manual", "voice:ended", "sms:lead_accepted", "sms:lead_dispositioned", "sms:queue_updated"];
    const onEnded = (d: { reason?: string }) => {
      if (d?.reason === "voicemail") {
        setPop(null);
        setDropped("It was voicemail — the call was dropped. You're back on the line.");
      }
    };
    s.on("voice:ended", onEnded);
    s.on("voice:connected", onConnected);
    evts.forEach((e) => s.on(e, nudge));
    // Lead Manager's "Ready for calls" switch — same tab, no socket needed.
    window.addEventListener("voice:ready", nudge);
    return () => {
      clearInterval(id);
      window.removeEventListener("voice:ready", nudge);
      s.off("voice:connected", onConnected);
      s.off("voice:ended", onEnded);
      evts.forEach((e) => s.off(e, nudge));
    };
  }, [refresh, admin, fastPoll]);

  // 1s ticker for the call timer.
  useEffect(() => {
    const id = setInterval(() => tick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  function showPop(card: Card) {
    const key = card.call?.id;
    if (!key) return;
    let seen: string | null = null;
    try {
      seen = sessionStorage.getItem(SHOWN_KEY);
    } catch {
      /* storage blocked */
    }
    if (seen === key) return;
    try {
      sessionStorage.setItem(SHOWN_KEY, key);
    } catch {
      /* storage blocked */
    }
    leadOfferedSound();
    setPop(card);
  }

  // Poll fallback for the drop: the popped call is no longer this agent's.
  useEffect(() => {
    if (!pop || !ds) return;
    if (!ds.call || ds.call.call?.id !== pop.call?.id) {
      setPop(null);
      setDropped("The call ended before you picked it up (voicemail).");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ds?.call?.call?.id]);
  useEffect(() => {
    if (!dropped) return;
    const t = setTimeout(() => setDropped(null), 6000);
    return () => clearTimeout(t);
  }, [dropped]);

  // Poll fallback for the pop (socket down / missed event).
  useEffect(() => {
    if (ds?.call && ds.call.call?.status === "CONNECTED") showPop(ds.call);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ds?.call?.call?.id, ds?.call?.call?.status]);

  // Keep the line open exactly while the agent can take a call: Ready for
  // calls, not on a manual (Dial drawer) call, and free (or already on an
  // auto-dialed call). A regular pool lead
  // also puts them ON_CALL, but with no dialer call — line closed then.
  const wantLine =
    !!ds && ds.enabled && ds.available && ds.agent.ready && !ds.manual &&
    (ds.agent.status === "AVAILABLE" || (ds.agent.status === "ON_CALL" && !!ds.call));
  useEffect(() => {
    if (admin || !ds) return;
    if (wantLine) {
      if ((line === "off" || line === "error") && Date.now() >= retryAt.current) {
        retryAt.current = Date.now() + 5000; // back off between reconnects
        openLine(ds.simulate);
      }
    } else if (line !== "off") {
      closeLine();
    }
  }, [wantLine, line, ds, admin]);

  const live = ds?.call && ds.call.call?.status === "CONNECTED" ? ds.call : null;

  // Leaving the SMS app tears down the browser's line and drops the customer.
  // While a customer is on the line: warn on unload, and open any link that
  // would leave the app (e.g. Log Sale) in a new tab instead.
  useEffect(() => {
    if (!live) return;
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const onClick = (e: MouseEvent) => {
      const a = (e.target as HTMLElement | null)?.closest?.("a");
      const href = a?.getAttribute("href") || "";
      if (!a || !href || href.startsWith("#") || a.target === "_blank") return;
      e.preventDefault();
      window.open(a.href, "_blank", "noopener");
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [live]);

  async function hangup() {
    setBusy(true);
    try {
      await api("/voice/dialer/hangup", { method: "POST" });
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function toggleMute() {
    const next = !muted;
    await setMuted(next);
    setMutedState(next);
  }

  if (admin || !ds || !ds.enabled || !ds.available) return null;
  if (!wantLine && !live) return null;

  const dot =
    live ? "bg-success" : line === "ready" ? "bg-success" : line === "connecting" ? "bg-pending" : "bg-danger";

  return (
    <>
      {/* Line dock — bottom-right, every SMS route */}
      <div className="fixed bottom-4 right-4 z-[9400] flex max-w-[calc(100vw-2rem)] items-center gap-2.5 rounded-xl border border-hairline bg-white px-3.5 py-2.5 text-sm text-ink shadow-xl">
        <span className={`h-2.5 w-2.5 flex-shrink-0 rounded-full ${dot} ${live || line === "connecting" ? "animate-pulse" : ""}`} />
        {live ? (
          <>
            <span className="min-w-0 truncate font-semibold">
              On call · {live.customer_name || formatPhone(live.phone_number)}
            </span>
            <span className="tabular-nums text-ink-muted">{clock(live.call.connected_at)}</span>
            {!ds.simulate && (
              <button
                onClick={toggleMute}
                className="rounded-lg border border-hairline px-2.5 py-1 text-xs font-semibold text-ink-muted hover:bg-black/5"
              >
                {muted ? "Unmute" : "Mute"}
              </button>
            )}
            <button
              onClick={hangup}
              disabled={busy}
              className="rounded-lg bg-danger px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50"
            >
              Hang up
            </button>
          </>
        ) : (
          <span className={dropped ? "font-semibold text-pending" : line === "mic_blocked" || line === "error" ? "text-danger" : "text-ink-muted"}>
            {dropped || LINE_LABEL[line]}
            {ds.simulate && line === "ready" ? " (simulator)" : ""}
          </span>
        )}
      </div>

      {/* The screen pop — the agent is already talking to this person. */}
      {pop && (
        <div className="fixed inset-0 z-[9500] flex items-center justify-center p-5" style={{ background: "rgba(10,14,22,.55)" }}>
          <div
            className="overflow-hidden"
            style={{ width: "min(460px, 94vw)", background: "#fff", color: "#1A1F2A", borderRadius: 16, boxShadow: "0 24px 60px rgba(0,0,0,.35)", maxHeight: "92vh", overflowY: "auto" }}
          >
            <div className="flex items-center gap-2.5" style={{ background: "#DCF0E3", padding: "14px 20px" }}>
              <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-success" />
              <h3 className="m-0 font-bold" style={{ fontSize: 15 }}>
                {pop.call?.direction === "inbound" ? "Incoming call · connected" : "Live call · connected"}
              </h3>
              {pop.list_name && (
                <span
                  className="ml-auto max-w-[45%] truncate rounded-full bg-accent/12 px-2 py-0.5 text-[11px] font-semibold text-accent"
                  title={pop.list_name}
                >
                  📋 {pop.list_name}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3" style={{ padding: "18px 20px 6px" }}>
              <div
                className="flex flex-shrink-0 items-center justify-center font-bold"
                style={{ width: 48, height: 48, borderRadius: "50%", background: "var(--a92)", color: "var(--accent)", fontSize: 16 }}
              >
                {initials(pop.customer_name)}
              </div>
              <div className="min-w-0">
                <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.15 }}>{pop.customer_name || "Unknown caller"}</div>
                <div style={{ fontSize: 15, fontWeight: 600, color: "#5A6473", marginTop: 2 }}>{formatPhone(pop.phone_number)}</div>
                {(pop.state || pop.address) && (
                  <div style={{ fontSize: 13, color: "#5A6473", marginTop: 2 }}>
                    <span aria-hidden>📍 </span>
                    {pop.address || pop.state}
                  </div>
                )}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2" style={{ padding: "10px 20px 0" }}>
              <div className="rounded-lg border px-2.5 py-1.5" style={{ borderColor: "#EEF0F2" }}>
                <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: "#8A93A0" }}>DOB</div>
                <div className="text-sm font-medium">{pop.dob || "—"}</div>
              </div>
              <div className="rounded-lg border px-2.5 py-1.5" style={{ borderColor: "#EEF0F2" }}>
                <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: "#8A93A0" }}>State</div>
                <div className="text-sm font-medium">{pop.state || "—"}</div>
              </div>
            </div>
            {pop.yes_reply && (
              <div style={{ padding: "10px 20px 0" }}>
                <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: "#8A93A0" }}>Their reply</div>
                <div className="mt-1 inline-block rounded-2xl rounded-bl-sm px-3 py-1.5 text-sm" style={{ background: "#F1F3F5" }}>
                  {pop.yes_reply}
                </div>
              </div>
            )}
            {pop.notes && (
              <div style={{ padding: "10px 20px 0", fontSize: 13, color: "#5A6473" }}>
                <b>Notes:</b> {pop.notes}
              </div>
            )}
            {pop.details && pop.details.length > 0 && (
              <div style={{ padding: "10px 20px 0" }}>
                <DetailGrid fields={pop.details} limit={6} dense />
              </div>
            )}
            <div className="flex" style={{ padding: "16px 20px 18px", gap: 8 }}>
              <button
                onClick={() => {
                  setPop(null);
                  if (!location.hash.startsWith("#/queue")) location.hash = "#/queue";
                }}
                className="flex-1"
                style={{ background: "#16a34a", color: "#fff", border: 0, borderRadius: 10, padding: 13, fontSize: 15, fontWeight: 700, cursor: "pointer" }}
              >
                Open lead
              </button>
              <button
                onClick={() => {
                  setPop(null);
                  hangup();
                }}
                disabled={busy}
                className="disabled:opacity-50"
                style={{ background: "#fff", color: "#DC2626", border: "1px solid rgba(220,38,38,.5)", borderRadius: 10, padding: "11px 16px", fontSize: 14, fontWeight: 700, cursor: "pointer" }}
              >
                Hang up
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

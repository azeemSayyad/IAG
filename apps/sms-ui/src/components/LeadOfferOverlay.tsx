import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { isAdmin } from "../lib/auth";
import { getSocket } from "../lib/socket";
import { leadOfferedSound, unlockSound } from "../lib/sound";

/* Global blocking lead-offer modal for the SMS app.

   Mounted once in PortalShell so it covers EVERY /sms route (Queue, Manager,
   Monitoring) — the agent can't keep working until they act. Three actions
   mirror the Gamified modal:
     • ACCEPT LEAD  → take the lead, work the chat
     • PASS         → release it back to the pool, stay available
     • NOT WORKING  → step away on a break (pick a reason); the lead is released

   Driven by polling /sms/queue/current (persists across navigation) + realtime
   nudges. */

type Lead = {
  id: string;
  phone_number: string;
  customer_name: string | null;
  address: string | null;
  last_message: string | null;
  priority: string;
  status: string;
};

const POLL_MS = 5000;
const BREAK_REASONS = ["Lunch", "Bathroom", "Meeting", "Personal", "Other"];

function initials(name: string | null): string {
  if (!name) return "•";
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] || "") + (parts.length > 1 ? parts[parts.length - 1][0] || "" : "")).toUpperCase() || "•";
}

export default function LeadOfferOverlay() {
  const [lead, setLead] = useState<Lead | null>(null);
  const [busy, setBusy] = useState(false);
  const [chooseReason, setChooseReason] = useState(false);
  const [otherReason, setOtherReason] = useState("");
  const [showOther, setShowOther] = useState(false);
  const shownId = useRef<string | null>(null);
  // Admins never work the queue, so they never get the offer popup.
  const admin = isAdmin();

  const check = useCallback(async () => {
    if (admin) return;
    try {
      const r = await api<{ lead: Lead | null }>("/sms/queue/current");
      const l = r.lead;
      if (l && l.status === "ASSIGNED") {
        if (shownId.current !== l.id) {
          shownId.current = l.id;
          leadOfferedSound(); // chime only when a NEW lead appears
        }
        setLead(l);
      } else {
        shownId.current = null;
        setLead(null);
        setChooseReason(false);
      }
    } catch {
      /* transient; next poll retries */
    }
  }, [admin]);

  useEffect(() => {
    if (admin) return;
    check();
    const id = setInterval(check, POLL_MS);
    const s = getSocket();
    const nudge = () => check();
    const evts = [
      "sms:lead_assigned",
      "sms:lead_accepted",
      "sms:lead_dispositioned",
      "sms:queue_updated",
    ];
    evts.forEach((e) => s.on(e, nudge));
    return () => {
      clearInterval(id);
      evts.forEach((e) => s.off(e, nudge));
    };
  }, [check, admin]);

  if (admin || !lead) return null;

  const clear = () => {
    shownId.current = null;
    setChooseReason(false);
    setShowOther(false);
    setOtherReason("");
    setLead(null);
  };

  const accept = async () => {
    setBusy(true);
    try {
      unlockSound();
      await api(`/sms/queue/accept/${lead.id}`, { method: "POST" });
      clear();
      if (!location.hash.startsWith("#/queue")) location.hash = "#/queue";
    } finally {
      setBusy(false);
    }
  };

  const pass = async () => {
    setBusy(true);
    try {
      await api(`/sms/queue/pass/${lead.id}`, { method: "POST" });
      clear();
    } finally {
      setBusy(false);
    }
  };

  // NOT WORKING: release the offered lead, then put the agent on a break with
  // the chosen reason so they stop being offered leads.
  const notWorking = async (reason: string) => {
    setBusy(true);
    try {
      await api(`/sms/queue/pass/${lead.id}`, { method: "POST" });
      await api("/sms/queue/break/start", {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      clear();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[9500] flex items-center justify-center p-5"
      style={{ background: "rgba(10,14,22,.55)" }}
    >
      <div
        className="overflow-hidden"
        style={{ width: "min(420px, 94vw)", background: "#fff", color: "#1A1F2A", borderRadius: 16, boxShadow: "0 24px 60px rgba(0,0,0,.35)" }}
      >
        {/* Light header bar — "New lead" with a person-add icon */}
        <div
          className="flex items-center gap-2.5"
          style={{ background: "var(--a92)", color: "#1A1F2A", padding: "14px 20px" }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" style={{ stroke: "var(--accent-ink)" }} strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="9" cy="8" r="3.5" />
            <path d="M3.5 20c0-3.3 2.7-6 6-6" />
            <path d="M17 9v6M14 12h6" />
          </svg>
          <h3 className="m-0 font-bold" style={{ fontSize: 15 }}>New lead</h3>
        </div>
        <div className="flex items-center gap-3" style={{ padding: "18px 20px 6px" }}>
          <div
            className="flex flex-shrink-0 items-center justify-center font-bold"
            style={{ width: 48, height: 48, borderRadius: "50%", background: "var(--a92)", color: "var(--accent)", fontSize: 16, letterSpacing: ".02em" }}
          >
            {initials(lead.customer_name)}
          </div>
          <div className="min-w-0">
            {lead.customer_name && (
              <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.15 }}>{lead.customer_name}</div>
            )}
            <div style={{ fontSize: 15, fontWeight: 600, color: "#5A6473", marginTop: 2 }}>{lead.phone_number}</div>
          </div>
        </div>

        {!chooseReason ? (
          <div className="flex flex-col" style={{ padding: "14px 20px 18px", gap: 8 }}>
            <button
              onClick={accept}
              disabled={busy}
              className="w-full disabled:opacity-50"
              style={{ background: "#16a34a", color: "#fff", border: 0, borderRadius: 10, padding: 13, fontSize: 15, fontWeight: 700, cursor: "pointer" }}
            >
              Accept lead
            </button>
            <div className="flex" style={{ gap: 8 }}>
              <button
                onClick={pass}
                disabled={busy}
                className="flex-1 disabled:opacity-50"
                style={{ background: "#fff", color: "#5A6473", border: "1px solid #D7DBE0", borderRadius: 10, padding: 11, fontSize: 14, fontWeight: 700, cursor: "pointer" }}
              >
                Pass
              </button>
              <button
                onClick={() => setChooseReason(true)}
                disabled={busy}
                className="flex-1 disabled:opacity-50"
                style={{ background: "#fff", color: "#DC2626", border: "1px solid rgba(220,38,38,.5)", borderRadius: 10, padding: 11, fontSize: 14, fontWeight: 700, cursor: "pointer" }}
              >
                Not working
              </button>
            </div>
          </div>
        ) : (
          <div style={{ background: "#fff", borderTop: "1px solid #EEF0F2", padding: "14px 20px 16px" }}>
            <div className="text-center" style={{ fontSize: 13, fontWeight: 800, color: "#5A6473", marginBottom: 10 }}>
              Choose a reason
            </div>
            <div className="flex flex-wrap justify-center" style={{ gap: 7 }}>
              {["Lunch", "Bathroom", "Personal"].map((r) => (
                <button
                  key={r}
                  onClick={() => notWorking(r)}
                  disabled={busy}
                  className="disabled:opacity-50"
                  style={{ border: "1px solid #D7DBE0", background: "#fff", color: "#1A1F2A", borderRadius: 999, padding: "7px 15px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}
                >
                  {r}
                </button>
              ))}
              <button
                onClick={() => setShowOther(true)}
                disabled={busy}
                className="disabled:opacity-50"
                style={{ border: "1px solid #D7DBE0", background: showOther ? "#F3F4F6" : "#fff", color: "#1A1F2A", borderRadius: 999, padding: "7px 15px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}
              >
                Other
              </button>
            </div>
            {showOther && (
              <div className="flex" style={{ gap: 8, marginTop: 10 }}>
                <input
                  autoFocus
                  value={otherReason}
                  onChange={(e) => setOtherReason(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && otherReason.trim() && !busy) notWorking(otherReason.trim()); }}
                  placeholder="Type a reason…"
                  style={{ flex: 1, minWidth: 0, border: "1px solid #D7DBE0", borderRadius: 10, padding: "9px 12px", fontSize: 12, color: "#1A1F2A", outline: "none" }}
                />
                <button
                  onClick={() => notWorking(otherReason.trim() || "Other")}
                  disabled={busy || !otherReason.trim()}
                  style={{ background: "#1A1F2A", color: "#fff", border: 0, borderRadius: 10, padding: "9px 16px", fontSize: 12, fontWeight: 700, cursor: "pointer", opacity: busy || !otherReason.trim() ? 0.5 : 1 }}
                >
                  Go
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

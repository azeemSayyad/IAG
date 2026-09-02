import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { isAdmin } from "../lib/auth";
import { formatPhone } from "../lib/phone";
import { getSocket } from "../lib/socket";
import {
  initSound,
  inboundTick,
  isMuted,
  leadOfferedSound,
  setMuted,
  unlockSound,
} from "../lib/sound";

type Lead = {
  id: string;
  phone_number: string;
  customer_name: string | null;
  address: string | null;
  last_message: string | null;
  priority: string;
  status: string;
  message_count: number;
  accepted_at: string | null;
  created_at: string | null;
};
type Status = {
  status: string;
  current_lead_id: string | null;
  consecutive_misses: number;
  total_leads_handled: number;
  total_appointments_set: number;
  break_reason?: string | null;
  break_started_at?: string | null;
  yes_waiting?: number;
};

const BREAK_REASONS = ["Lunch", "Bathroom", "Meeting", "Personal", "Other"];
const BREAK_ICON: Record<string, string> = {
  Lunch: "🍔",
  Bathroom: "🚻",
  Meeting: "👥",
  Personal: "☕",
  Other: "⏸️",
};

function elapsedSince(iso?: string | null): string {
  if (!iso) return "0:00";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}
type Msg = {
  id: string;
  direction: string;
  body: string;
  sender_type: string | null;
  created_at: string | null;
};

const POLL_MS = 3000;

const DISPOSITIONS: { value: string; label: string; tone: string }[] = [
  { value: "SALE", label: "Sale", tone: "bg-success text-white" },
  { value: "APPOINTMENT_SET", label: "Appointment Set", tone: "bg-accent text-white" },
  { value: "ATTEMPTED", label: "Attempted", tone: "bg-black/5 text-ink" },
  { value: "COULDNT_SELL", label: "Couldn't Sell", tone: "bg-black/5 text-ink" },
  { value: "NOT_INTERESTED", label: "Not Interested", tone: "bg-black/5 text-ink" },
  // Lead is only eligible for Medicare (which we don't sell). Stored in the DB
  // so Medicare leads can be pulled separately later, but intentionally kept out
  // of the manager/admin dashboards, stats, leaderboards and conversion rate.
  { value: "ELIGIBLE_FOR_MEDICARE", label: "Medicare", tone: "bg-danger/10 text-danger" },
  { value: "WRONG_NUMBER", label: "Wrong Number", tone: "bg-danger/10 text-danger" },
  { value: "UNQUALIFIED", label: "Unqualified", tone: "bg-danger/10 text-danger" },
];

const PRIORITY_TONE: Record<string, string> = {
  HOT: "bg-danger/12 text-danger",
  WARM: "bg-pending/12 text-pending",
  NORMAL: "bg-black/5 text-ink-muted",
};
const STATUS_TONE: Record<string, string> = {
  AVAILABLE: "bg-success/12 text-success",
  ON_CALL: "bg-accent/12 text-accent",
  AWAY: "bg-pending/12 text-pending",
  OFFLINE: "bg-black/5 text-ink-faint",
};

const isDev = import.meta.env.DEV;

type Stats = {
  today: { leads_handled: number; avg_response_ms: number; appointments: number; sold: number; conversion_pct: number };
  scorecard: {
    leads_received: number; accepted: number; missed: number; appointments: number; sold: number;
    avg_response_ms: number; break_seconds: number; active_seconds: number; conversion_pct: number;
  };
};

const fmtResp = (ms: number) => (ms ? `${Math.round(ms / 1000)}s` : "0s");
const fmtDur = (s: number) => {
  const m = Math.floor((s || 0) / 60);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
};

function StatCard({ label, value, icon }: { label: string; value: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-hairline p-4">
      <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ink-faint">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold text-ink">{value}</div>
    </div>
  );
}

// Small line icons for the "How the day was spent" cards (inherit currentColor).
const ICO = "h-4 w-4 flex-shrink-0";
const IconClock = () => (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={ICO}><circle cx="12" cy="12" r="9" /><path d="M12 7.5V12l3 2" /></svg>);
const IconBolt = () => (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={ICO}><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" /></svg>);
const IconCoffee = () => (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={ICO}><path d="M17 8h1a3 3 0 0 1 0 6h-1" /><path d="M3 8h14v5a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5V8Z" /><path d="M6 1v2M10 1v2M14 1v2" /></svg>);
const IconPlay = () => (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={ICO}><path d="M7 4.5v15l12-7.5-12-7.5Z" /></svg>);
const IconPhone = () => (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={ICO}><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.1 9.9a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92Z" /></svg>);
const IconLeave = () => (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={ICO}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5" /><path d="M21 12H9" /></svg>);
const IconChecks = () => (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5 flex-shrink-0"><path d="m1 13 4 4 7-8" /><path d="m9 13 4 4 8-9" /></svg>);

function pctOf(n: number, d: number) {
  return d > 0 ? Math.round((n / d) * 100) : 0;
}

// Donut ring stat — uses the app palette via CSS-var strokes (theme/dark aware).
function Donut({ pct, label, sub, color }: { pct: number; label: string; sub: string; color: string }) {
  const R = 52;
  const C = 2 * Math.PI * R;
  const p = Math.min(100, Math.max(0, pct));
  const off = C * (1 - p / 100);
  return (
    <div className="flex flex-col items-center p-2">
      <div className="relative h-32 w-32">
        <svg viewBox="0 0 128 128" className="h-full w-full -rotate-90">
          <circle cx="64" cy="64" r={R} fill="none" strokeWidth="11" style={{ stroke: "var(--color-hairline, rgba(26,31,42,0.10))" }} />
          <circle
            cx="64" cy="64" r={R} fill="none" strokeWidth="11" strokeLinecap="round"
            style={{ stroke: color, strokeDasharray: C, strokeDashoffset: off, transition: "stroke-dashoffset .5s ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-2xl font-bold text-ink">{pct}%</div>
          <div className="text-xs text-ink-faint">{sub}</div>
        </div>
      </div>
      <div className="mt-3 text-sm font-medium text-ink-muted">{label}</div>
    </div>
  );
}

// "Fresh on accept": the first time an agent opens a given lead's thread we
// record how many messages already existed and only ever show messages added
// after that point — so an accepted lead starts with an empty conversation,
// with no backend change. Persisted per-lead so it survives reloads while the
// thread keeps building live from the agent's first message onward.
function freshOffset(leadId: string | number, total: number): number {
  const k = `sms_fresh_${leadId}`;
  const saved = localStorage.getItem(k);
  if (saved === null) {
    localStorage.setItem(k, String(total));
    return total;
  }
  return Number(saved) || 0;
}

export default function SmsQueue() {
  const [status, setStatus] = useState<Status | null>(null);
  const [current, setCurrent] = useState<Lead | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [myLeads, setMyLeads] = useState<Lead[]>([]);
  const [myLeadsOpen, setMyLeadsOpen] = useState(false); // "My recent leads" collapsed by default
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [breakMenu, setBreakMenu] = useState(false);
  const [muted, setMutedState] = useState(false);
  const [stats, setStats] = useState<Stats | null>(null);
  const [apptOpen, setApptOpen] = useState(false);
  const [apptLocal, setApptLocal] = useState("");
  const [, setTick] = useState(0); // 1s ticker for the break timer
  const chatEndRef = useRef<HTMLDivElement>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);

  // Local "on break" override: keeps the on-break header showing the instant a
  // reason is picked, even if the break endpoint is slow/unbacked (preview). The
  // status poll respects it until the server confirms AWAY, or the agent resumes/leaves.
  const breakOverrideRef = useRef<{ reason: string; at: string } | null>(null);
  const refresh = useCallback(async () => {
    try {
      const [s, c, m, st] = await Promise.all([
        api<Status>("/sms/queue/status"),
        api<{ lead: Lead | null }>("/sms/queue/current"),
        api<{ items: Lead[] }>("/sms/queue/my-leads?limit=25"),
        api<Stats>("/sms/queue/my-stats"),
      ]);
      const ov = breakOverrideRef.current;
      let nextStatus = s;
      if (ov) {
        if (s.status === "AWAY" || s.status === "OFFLINE") {
          breakOverrideRef.current = null; // server confirmed the break (or we left)
        } else {
          nextStatus = { ...s, status: "AWAY", break_reason: ov.reason, break_started_at: ov.at };
        }
      }
      setStatus(nextStatus);
      setCurrent(c.lead);
      setMyLeads(m.items);
      setStats(st);
      if (c.lead && c.lead.status === "IN_PROGRESS") {
        const conv = await api<{ items: Msg[] }>(
          `/sms/queue/conversation/${c.lead.id}`,
        );
        const off = freshOffset(c.lead.id, conv.items.length);
        setMessages(conv.items.slice(off));
      } else {
        setMessages([]);
      }
    } catch {
      /* transient; next poll retries */
    }
  }, []);

  // Load saved mute preference + unlock audio on the first user interaction
  // (so a lead arriving for an already-joined agent still beeps).
  useEffect(() => {
    initSound();
    setMutedState(isMuted());
    const onFirst = () => unlockSound();
    window.addEventListener("pointerdown", onFirst, { once: true });
    return () => window.removeEventListener("pointerdown", onFirst);
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    const s = getSocket();
    const nudge = () => refresh();
    // A lead offered to me → attention chime.
    const onAssigned = () => {
      leadOfferedSound();
      refresh();
    };
    // A new customer reply → soft tick (skip my own outbound sends).
    const onMessage = (m: { direction?: string }) => {
      if (m && m.direction === "INBOUND") inboundTick();
      refresh();
    };
    const plain = ["sms:lead_accepted", "sms:lead_dispositioned", "sms:queue_updated"];
    s.on("sms:lead_assigned", onAssigned);
    s.on("sms:new_message", onMessage);
    plain.forEach((e) => s.on(e, nudge));
    return () => {
      clearInterval(id);
      s.off("sms:lead_assigned", onAssigned);
      s.off("sms:new_message", onMessage);
      plain.forEach((e) => s.off(e, nudge));
    };
  }, [refresh]);

  useEffect(() => {
    // Pin the chat thread to its latest message by scrolling ONLY its own
    // container. scrollIntoView() scrolls every scrollable ancestor (incl. the
    // window), which jumped the whole page to the middle when a lead was
    // accepted and the queue opened. Scrolling the container alone never moves
    // the page.
    const el = chatScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  // 1s ticker so the break timer counts up live.
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  const join = () => {
    unlockSound(); // user gesture — enables audio for this session
    return act(() => api("/sms/queue/join", { method: "POST" }));
  };

  // Auto-join ONCE when an agent lands here right after logging a sale: add-deal.html
  // redirects to #/queue?autoJoin=1 so the rep is put back into rotation immediately.
  // We consume the flag THROUGH the router (setSearchParams) — not raw
  // history.replaceState, which desynced the HashRouter and left the flag in the
  // router's location, re-firing the auto-join so an agent who LEFT kept getting
  // auto-rejoined. Consuming it via the router removes it everywhere, so leaving sticks.
  const [searchParams, setSearchParams] = useSearchParams();
  const autoJoinedRef = useRef(false);
  useEffect(() => {
    if (autoJoinedRef.current || !status) return;
    if (searchParams.get("autoJoin") !== "1") return;
    autoJoinedRef.current = true;
    setSearchParams({}, { replace: true });           // clear the flag from the router's location
    if ((status.status ?? "OFFLINE") === "OFFLINE") join();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, searchParams]);
  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    setMutedState(next);
    if (!next) {
      unlockSound();
      leadOfferedSound(); // preview when un-muting
    }
  };
  const leave = () => {
    breakOverrideRef.current = null;
    return act(() => api("/sms/queue/leave", { method: "POST" }));
  };
  const startBreak = (reason: string) => {
    setBreakMenu(false);
    // Optimistically show the on-break header immediately.
    const at = new Date().toISOString();
    breakOverrideRef.current = { reason, at };
    setStatus((prev) => (prev ? { ...prev, status: "AWAY", break_reason: reason, break_started_at: at } : prev));
    return act(() =>
      api("/sms/queue/break/start", {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
    );
  };
  const endBreak = () => {
    breakOverrideRef.current = null;
    setStatus((prev) => (prev ? { ...prev, status: "AVAILABLE", break_reason: null, break_started_at: null } : prev));
    return act(() => api("/sms/queue/break/end", { method: "POST" }));
  };
  // Accept/Pass for an offered lead now live in the global LeadOfferOverlay
  // (blocking modal), so the queue page only handles the active chat.
  const disposition = (id: string, value: string, appointmentTime?: string) =>
    act(() =>
      api(`/sms/queue/disposition/${id}`, {
        method: "POST",
        body: JSON.stringify({
          disposition: value,
          ...(appointmentTime ? { appointment_time: appointmentTime } : {}),
        }),
      }),
    );

  // "Appointment Set" first asks for a date/time, then dispositions + books a
  // real appointment (which the portal then reminds on at 24h/1h/15m).
  const onDisposition = (id: string, value: string) => {
    if (value === "APPOINTMENT_SET") {
      setApptOpen(true);
      return;
    }
    disposition(id, value);
  };
  const confirmAppt = () => {
    if (!current || !apptLocal) return;
    const iso = new Date(apptLocal).toISOString(); // local picker → UTC ISO
    setApptOpen(false);
    setApptLocal("");
    disposition(current.id, "APPOINTMENT_SET", iso);
  };

  async function send() {
    if (!draft.trim() || !current) return;
    const body = draft.trim();
    setDraft("");
    await act(() =>
      api(`/sms/queue/send/${current.id}`, {
        method: "POST",
        body: JSON.stringify({ body }),
      }),
    );
  }

  function simulateReply() {
    if (!current) return;
    act(() =>
      api(`/sms/queue/dev/simulate-inbound/${current.id}`, {
        method: "POST",
        body: JSON.stringify({ body: "Sounds good, tell me more" }),
      }),
    );
  }

  const st = status?.status ?? "OFFLINE";
  const admin = isAdmin(); // admins never join the queue or take leads
  const inChat = current && current.status === "IN_PROGRESS";

  // Sound / mute toggle — shown in both the Available and On-break headers.
  const muteBtn = (
    <button
      onClick={toggleMute}
      title={muted ? "Sound off — click to enable lead alerts" : "Sound on — click to mute"}
      aria-label={muted ? "Unmute notifications" : "Mute notifications"}
      className={`flex h-9 w-9 items-center justify-center rounded-lg text-base ${muted ? "bg-black/5 text-ink-faint" : "bg-accent/12 text-accent"}`}
    >
      {muted ? "🔇" : "🔊"}
    </button>
  );

  return (
    <div className="space-y-4">
      {/* Waiting-for-next-lead — thin bar above the Lead Manager control bar */}
      {st === "AVAILABLE" && !current && (
        <div className="flex items-center justify-center gap-2.5 rounded-xl bg-accent/10 px-4 py-2 text-sm font-medium text-accent">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-accent" />
          </span>
          Waiting for the next lead…
        </div>
      )}
      {/* Control bar — turns amber while on break (matches the reference design) */}
      <div
        className={`relative z-30 flex flex-wrap items-center gap-3 rounded-2xl border p-4 ${
          st === "AWAY" ? "border-pending/30 bg-pending/10" : "glass border-transparent"
        }`}
      >
        <h2 className="text-xl font-semibold text-ink">Lead Manager</h2>

        {admin ? (
          <span className="rounded-full bg-black/5 px-3 py-1 text-xs font-medium text-ink-muted">Admin — view only</span>
        ) : st === "AWAY" ? (
          /* ── On-break state ── */
          <>
            <span className="flex items-center gap-1.5 rounded-full bg-pending/20 px-3 py-1 text-xs font-semibold text-pending">
              <span className="h-2 w-2 rounded-full bg-pending" />
              On break · {status?.break_reason || "Break"}
            </span>
            <span className="text-sm font-semibold tabular-nums text-pending">{elapsedSince(status?.break_started_at)}</span>
            <div className="ml-auto flex items-center gap-2">
              {muteBtn}
              <button
                onClick={endBreak}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50"
              >
                <IconPlay /> Resume
              </button>
              <button
                onClick={leave}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-lg border border-hairline bg-white px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink disabled:opacity-50"
              >
                <IconLeave /> Leave
              </button>
            </div>
          </>
        ) : st === "OFFLINE" ? (
          /* ── Offline state ── */
          <>
            <span className="flex items-center gap-1.5 rounded-full bg-black/5 px-3 py-1 text-xs font-semibold text-ink-muted">
              <span className="h-2 w-2 rounded-full bg-ink-faint" /> Offline
            </span>
            <div className="ml-auto flex items-center gap-2">
              {muteBtn}
              <button
                onClick={join}
                disabled={busy}
                className="rounded-lg bg-accent px-6 py-2.5 text-base font-semibold text-white hover:bg-accent-hover disabled:opacity-50"
              >
                Join queue
              </button>
            </div>
          </>
        ) : (
          /* ── Available / On-call state ── */
          <>
            <span className="flex items-center gap-1.5 rounded-full bg-success/15 px-3 py-1 text-xs font-semibold text-success">
              <span className="h-2 w-2 rounded-full bg-success" />
              {st === "ON_CALL" ? "On call" : "Available"}
            </span>
            <span
              className="flex items-center gap-1.5 rounded-full bg-success/12 px-3 py-1 text-xs font-semibold text-success"
              title="Positive leads waiting in the shared pool"
            >
              <IconChecks /> {status?.yes_waiting ?? 0} in queue
            </span>
            <span className="text-xs text-ink-muted">
              Handled <strong className="text-sm text-ink">{status?.total_leads_handled ?? 0}</strong>
            </span>
            <span className="text-xs text-ink-muted">
              Appts <strong className="text-sm text-ink">{status?.total_appointments_set ?? 0}</strong>
            </span>
            <div className="ml-auto flex items-center gap-2">
              {muteBtn}
              <div className="relative">
                <button
                  onClick={() => setBreakMenu((v) => !v)}
                  disabled={busy || st === "ON_CALL"}
                  aria-haspopup="menu"
                  aria-expanded={breakMenu}
                  className="flex items-center gap-1.5 rounded-lg bg-pending/15 px-3 py-2 text-sm font-semibold text-pending transition-colors hover:bg-pending/25 disabled:opacity-40"
                >
                  <IconCoffee /> Break
                  <span className={`text-[10px] transition-transform ${breakMenu ? "rotate-180" : ""}`}>▾</span>
                </button>
                {breakMenu && (
                  <>
                    {/* click-away */}
                    <div className="fixed inset-0 z-40" onClick={() => setBreakMenu(false)} />
                    <div
                      role="menu"
                      className="absolute right-0 z-50 mt-2 w-52 overflow-hidden rounded-xl border border-hairline bg-white shadow-xl ring-1 ring-black/5"
                    >
                      <div className="border-b border-hairline-soft bg-pending/5 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-pending">
                        Choose a reason
                      </div>
                      {BREAK_REASONS.map((r) => (
                        <button
                          key={r}
                          role="menuitem"
                          onClick={() => startBreak(r)}
                          disabled={busy}
                          className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm font-medium text-ink transition-colors hover:bg-pending/10 disabled:opacity-50"
                        >
                          <span className="text-base leading-none">{BREAK_ICON[r]}</span>
                          {r}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
              <button
                onClick={leave}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-lg border border-hairline px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink disabled:opacity-50"
              >
                <IconLeave /> Leave
              </button>
            </div>
          </>
        )}
      </div>

      {st === "AWAY" && (
        <div className="glass rounded-2xl p-10 text-center text-sm text-ink-muted">
          You won't be offered leads until you end your break.
        </div>
      )}

      {/* Offered-lead popup is handled globally by LeadOfferOverlay (blocking
          modal that follows the agent across pages). */}

      {/* Active chat + disposition */}
      {inChat && current && (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="glass flex h-[28rem] flex-col rounded-2xl lg:col-span-2">
            <div className="flex items-center justify-between border-b border-hairline-soft p-4">
              <div>
                {current.customer_name && (
                  <div className="font-semibold text-ink">{current.customer_name}</div>
                )}
                {/* Phone — bigger, bolder, highlighted so it's easy to read/dial. */}
                <div className="mt-1">
                  <span className="inline-block rounded-lg bg-accent/10 px-3 py-1 text-xl font-bold tracking-wide text-accent">
                    {formatPhone(current.phone_number)}
                  </span>
                </div>
                {current.address && (
                  <div className="mt-1.5">
                    <span className="inline-flex items-center gap-1 rounded-lg bg-accent/10 px-3 py-1 text-sm font-bold text-accent">
                      <span aria-hidden>📍</span>
                      {current.address}
                    </span>
                  </div>
                )}
              </div>
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-semibold ${PRIORITY_TONE[current.priority]}`}
              >
                {current.priority}
              </span>
            </div>
            <div ref={chatScrollRef} className="flex-1 space-y-2 overflow-y-auto p-4">
              {messages.length === 0 && (
                <div className="py-8 text-center text-sm text-ink-faint">
                  No messages yet — start the conversation.
                </div>
              )}
              {messages.map((m) => {
                const out = m.direction === "OUTBOUND";
                return (
                  <div
                    key={m.id}
                    className={`flex ${out ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`max-w-[75%] rounded-2xl px-3 py-2 text-sm ${
                        out
                          ? "bg-accent text-white"
                          : "bg-white text-ink-soft border border-hairline-soft"
                      }`}
                    >
                      {m.body}
                    </div>
                  </div>
                );
              })}
              <div ref={chatEndRef} />
            </div>
            <div className="flex gap-2 border-t border-hairline-soft p-3">
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && send()}
                placeholder="Type a message…"
                className="flex-1 rounded-lg border border-hairline bg-white/70 px-3 py-2 text-sm outline-none focus:border-accent"
              />
              <button
                onClick={send}
                disabled={busy || !draft.trim()}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50"
              >
                Send
              </button>
              {isDev && (
                <button
                  onClick={simulateReply}
                  disabled={busy}
                  title="Dev only: fake a customer reply"
                  className="rounded-lg border border-hairline px-3 py-2 text-xs text-ink-faint"
                >
                  ↩ Reply
                </button>
              )}
            </div>
          </div>

          <div className="glass rounded-2xl p-4">
            <h3 className="mb-3 text-sm font-semibold text-ink">Disposition</h3>
            <div className="space-y-2">
              {DISPOSITIONS.map((d) => (
                <button
                  key={d.value}
                  onClick={() => onDisposition(current.id, d.value)}
                  disabled={busy}
                  className={`w-full rounded-lg px-3 py-2 text-sm font-semibold disabled:opacity-50 ${d.tone}`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Today's Scorecard — donuts (no container) + "how the day was spent" cards */}
      {stats && (
        <div className="glass rounded-2xl p-5">
          <h3 className="mb-3 text-sm font-semibold text-ink">Today's Scorecard</h3>
          <div className="flex flex-wrap justify-center gap-10 sm:gap-20">
            <Donut
              pct={pctOf(stats.scorecard.accepted, stats.scorecard.leads_received)}
              label="Accepted"
              sub={`${stats.scorecard.accepted} / ${stats.scorecard.leads_received}`}
              color="var(--color-success)"
            />
            <Donut
              pct={pctOf(stats.scorecard.missed, stats.scorecard.leads_received)}
              label="Missed"
              sub={`${stats.scorecard.missed} / ${stats.scorecard.leads_received}`}
              color="var(--color-danger)"
            />
            <Donut
              pct={stats.scorecard.conversion_pct}
              label="Conversion"
              sub={`${stats.scorecard.sold} sales`}
              color="var(--color-accent)"
            />
          </div>
          <h3 className="mb-2 mt-6 flex items-center gap-1.5 text-sm font-semibold text-ink">
            <IconClock /> How the day was spent
          </h3>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard icon={<IconBolt />} label="Avg Response" value={fmtResp(stats.scorecard.avg_response_ms)} />
            <StatCard icon={<IconCoffee />} label="Break Time" value={fmtDur(stats.scorecard.break_seconds)} />
            <StatCard icon={<IconPlay />} label="Active Time" value={fmtDur(stats.scorecard.active_seconds)} />
            <StatCard icon={<IconPhone />} label="Leads Handled" value={stats.today.leads_handled} />
          </div>
        </div>
      )}

      {/* My recent leads (collapsible — collapsed by default) */}
      <div className="glass rounded-2xl p-5">
        <button
          type="button"
          onClick={() => setMyLeadsOpen((v) => !v)}
          aria-expanded={myLeadsOpen}
          className="flex w-full items-center justify-between text-left"
        >
          <h3 className="text-sm font-semibold text-ink">
            My recent leads <span className="text-ink-faint">({myLeads.length})</span>
          </h3>
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            className={`h-4 w-4 text-ink-muted transition-transform ${myLeadsOpen ? "rotate-180" : ""}`}
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        {myLeadsOpen &&
          (myLeads.length === 0 ? (
            <div className="py-4 text-sm text-ink-muted">No leads yet.</div>
          ) : (
            <div className="mt-3 space-y-1.5">
              {myLeads.map((l) => (
              <div
                key={l.id}
                className="flex items-start justify-between gap-3 rounded-lg border border-hairline-soft px-3 py-2 text-sm"
              >
                <div className="min-w-0">
                  {l.customer_name && (
                    <div className="font-medium text-ink">{l.customer_name}</div>
                  )}
                  <div className="font-semibold text-accent">{formatPhone(l.phone_number)}</div>
                  {l.address && (
                    <div className="mt-0.5 flex items-center gap-1 text-xs text-ink-muted">
                      <span aria-hidden>📍</span>
                      <span className="truncate">{l.address}</span>
                    </div>
                  )}
                </div>
                <span className="flex flex-shrink-0 items-center gap-2 text-xs">
                  <span className="text-ink-faint">{l.status}</span>
                  {l.disposition && (
                    <span className="rounded bg-black/5 px-2 py-0.5 text-ink-muted">
                      {l.disposition}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
          ))}
      </div>

      {/* Appointment date/time picker (for "Appointment Set") */}
      {apptOpen && (
        <div className="fixed inset-0 z-[9500] flex items-center justify-center bg-black/40 p-4" onClick={() => setApptOpen(false)}>
          <div className="glass w-full max-w-sm rounded-2xl bg-white p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-1 text-lg font-semibold text-ink">Set appointment</h3>
            <p className="mb-3 text-sm text-ink-muted">Pick the date &amp; time. The customer gets auto reminders 24h, 1h &amp; 15m before.</p>
            <input
              type="datetime-local"
              value={apptLocal}
              onChange={(e) => setApptLocal(e.target.value)}
              className="mb-4 w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm outline-none focus:border-accent"
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setApptOpen(false)} className="rounded-lg border border-hairline px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink">Cancel</button>
              <button onClick={confirmAppt} disabled={busy || !apptLocal} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">Confirm appointment</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

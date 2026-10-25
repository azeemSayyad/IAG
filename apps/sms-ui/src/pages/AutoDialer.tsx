import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { getAccessToken, isAdminClass } from "../lib/auth";
import { DialButton } from "../components/DialDrawer";
import DialerNotes from "../components/DialerNotes";
import { formatPhone } from "../lib/phone";
import { getSocket } from "../lib/socket";

/* Auto Dialer (manager / admin) — the YES-reply auto dialer at a glance.

   Agents never come here: they take these calls inside Lead Manager. This page
   is for whoever runs the floor: is it on, who is on the line, who is waiting
   to be called and why, how today went, and the dialing rules. In dev without
   Vonage keys it also carries the simulator. Data: GET /voice/dialer/overview
   (polled every 4 s + nudged by voice:/sms: socket events). */

type Settings = {
  enabled: boolean;
  ring_seconds: number;
  max_attempts_24h: number;
  max_total_attempts: number;
  retry_minutes: number;
  reply_delay_seconds: number;
  window_start_hour: number;
  window_end_hour: number;
  business_name: string;
  callback_number: string;
  // Inbound calls (callbacks): hold limit + open hours.
  max_hold_seconds: number;
  hours_enabled: boolean;
  open_days: number[]; // Mon=0 … Sun=6
  open_start_minute: number;
  open_end_minute: number;
  hours_tz: string;
  hours_label: string;
  hold_offer_seconds: number;
  recording_notice: string;
};
type Attempt = {
  id: string;
  status: string;
  phone_number: string;
  from_number: string | null;
  direction: string;
  customer_name: string | null;
  agent_name: string | null;
  created_at: string | null;
  connected_at: string | null;
  duration_seconds: number | null;
  disposition: string | null;
  error: string | null;
  recording: "stored" | "pending" | "failed" | null;
  hold_for_name?: string | null;
};
type ReadyLead = {
  id: string;
  customer_name: string | null;
  phone_number: string;
  status: string;
  yes_reply: string | null;
  yes_at: string | null;
  next_dial_at: string | null;
  dial_attempts: number;
  state: string | null;
  waiting_for: "agent" | "retry" | "calling_hours" | null;
};
type AgentRow = {
  user_id: string;
  name: string;
  state: "on_call" | "waiting" | "busy" | "no_line" | "pool_only" | "break";
  line: boolean;
  since: string | null;
  call: { customer_name: string | null; phone_number: string; connected_at: string | null } | null;
};
type Overview = {
  settings: Settings;
  available: boolean;
  configured: boolean;
  simulate: boolean;
  sim_mode: "human" | "machine" | "no_answer" | "busy" | "manual" | null;
  sim_ignore_hours: boolean | null;
  fleet_size: number;
  ready_agents: number;
  agents_with_line: number;
  in_flight: number;
  today: {
    dialed: number;
    live_answers: number;
    connected: number;
    machine: number;
    no_answer: number;
    failed: number;
    abandoned: number;
    abandon_rate_pct: number;
    avg_detect_ms: number | null;
    avg_bridge_ms: number | null;
    avg_talk_seconds: number | null;
    opted_out: number;
    sales: number;
    appointments: number;
  };
  queue: { agent: number; retry: number; calling_hours: number; dialing: number };
  hours_open: { eastern: boolean; all_zones: boolean };
  agents: AgentRow[];
  ready: ReadyLead[];
  recent: Attempt[];
};

type CallPage = { items: Attempt[]; has_more: boolean; counts: Record<string, number> };

const CALLS_PAGE = 30;
type Period = "today" | "yesterday" | "week" | "month" | "all" | "custom";
const PERIODS: [Period, string][] = [
  ["today", "Today"], ["yesterday", "Yesterday"], ["week", "This week"],
  ["month", "This month"], ["all", "All time"], ["custom", "Custom"],
];
const PERIOD_SUB: Record<Period, string> = {
  today: "Since midnight Eastern",
  yesterday: "Yesterday, Eastern time",
  week: "Since Monday, Eastern time",
  month: "Since the 1st, Eastern time",
  all: "Every call so far",
  custom: "Eastern time",
};
const isoDay = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const ABANDON_LIMIT = 3;
const IN_FLIGHT = ["DIALING", "RINGING", "ANSWERED"];

// ---- formatting -------------------------------------------------------------

const hhmm = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
function when(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 86400000);
  const prefix = days === 0 ? "" : days === 1 ? "Tomorrow " : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} `;
  return prefix + hhmm(iso);
}
function clock(iso: string | null): string {
  if (!iso) return "0:00";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
const mmss = (s: number | null) => (s == null ? "—" : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
const secs = (ms: number | null) => (ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`);
const pct = (n: number, d: number) => (d ? Math.round((n / d) * 100) : 0);
const hourLabel = (h: number) => (h === 0 || h === 24 ? "12 am" : h === 12 ? "12 pm" : h < 12 ? `${h} am` : `${h - 12} pm`);

const RESULT: Record<string, { label: string; tone: string }> = {
  DIALING: { label: "Dialing", tone: "bg-pending/12 text-pending" },
  RINGING: { label: "Ringing", tone: "bg-pending/12 text-pending" },
  ANSWERED: { label: "Answered · checking", tone: "bg-pending/12 text-pending" },
  CONNECTED: { label: "On call now", tone: "bg-success/12 text-success" },
  COMPLETED: { label: "Talked to agent", tone: "bg-success/12 text-success" },
  MACHINE: { label: "Voicemail", tone: "bg-black/5 text-ink-muted" },
  NO_ANSWER: { label: "No answer", tone: "bg-black/5 text-ink-muted" },
  BUSY: { label: "Busy", tone: "bg-black/5 text-ink-muted" },
  CANCELLED: { label: "Cancelled", tone: "bg-black/5 text-ink-muted" },
  ABANDONED: { label: "No agent free", tone: "bg-danger/10 text-danger" },
  MISSED: { label: "Missed callback", tone: "bg-danger/10 text-danger" },
  HOLDING: { label: "On hold", tone: "bg-pending/12 text-pending" },
  BRIDGING: { label: "Connecting", tone: "bg-pending/12 text-pending" },
  FAILED: { label: "Failed", tone: "bg-danger/10 text-danger" },
};

// The result the agent set with Mark… — what the manager wants to see per call.
const DISPOSITION: Record<string, { label: string; tone: string }> = {
  SALE: { label: "Sale", tone: "bg-success text-white" },
  APPOINTMENT_SET: { label: "Appointment set", tone: "bg-accent text-white" },
  ATTEMPTED: { label: "Attempted", tone: "bg-black/5 text-ink-soft" },
  COULDNT_SELL: { label: "Couldn't sell", tone: "bg-black/5 text-ink-soft" },
  NOT_INTERESTED: { label: "Not interested", tone: "bg-black/5 text-ink-soft" },
  ELIGIBLE_FOR_MEDICARE: { label: "Medicare", tone: "bg-black/5 text-ink-soft" },
  VOICEMAIL: { label: "Voicemail (agent)", tone: "bg-black/5 text-ink-soft" },
  WRONG_NUMBER: { label: "Wrong number", tone: "bg-danger/10 text-danger" },
  UNQUALIFIED: { label: "Unqualified", tone: "bg-danger/10 text-danger" },
};

/** One call's outcome: the agent's result if set, else what the call did. */
function outcome(a: { status: string; disposition: string | null }): { label: string; tone: string } {
  if (a.disposition) return DISPOSITION[a.disposition] || { label: a.disposition.replace(/_/g, " ").toLowerCase(), tone: "bg-black/5 text-ink-soft" };
  if (a.status === "CONNECTED") return { label: "On call now", tone: "bg-success/12 text-success" };
  if (a.status === "COMPLETED") return { label: "Awaiting agent's result", tone: "bg-pending/12 text-pending" };
  return RESULT[a.status] || { label: a.status, tone: "bg-black/5 text-ink-muted" };
}

const SIM_RESULT: Record<string, { text: string; ok: boolean }> = {
  ready_to_call: { text: "A reply → on the dial list. Called after the short wait, as soon as an agent is free.", ok: true },
  yes_to_pool: { text: "The dialer is OFF, so this reply went to the regular Lead Manager pool. Turn the dialer on first.", ok: false },
  reply_saved: { text: "Already closed by an agent → saved to their thread, not called. Only a clear YES calls them again.", ok: true },
  stop: { text: "Opt-out → Do-Not-Call. Never called or texted again.", ok: true },
  negative: { text: "Said no / not interested → Do-Not-Call. Never called or texted again.", ok: true },
  abuse: { text: "Rude / abusive → Do-Not-Call. Never called or texted again.", ok: true },
  wrong: { text: "Wrong number → Do-Not-Call. Never called or texted again.", ok: true },
  yes_dnc: { text: "This number is on Do-Not-Call, so it is never dialed.", ok: false },
  yes_already_active: { text: "This person is already on a call or with an agent — no new call.", ok: false },
  duplicate: { text: "Same message already received — ignored.", ok: false },
};

// ---- building blocks ---------------------------------------------------------

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TIMEZONES: [string, string][] = [
  ["America/New_York", "Eastern"], ["America/Chicago", "Central"], ["America/Denver", "Mountain"],
  ["America/Phoenix", "Arizona"], ["America/Los_Angeles", "Pacific"],
];
const toHHMM = (m: number) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const fromHHMM = (v: string) => {
  const [h, m] = v.split(":").map(Number);
  return h * 60 + m;
};

function Card({ title, sub, right, pinned, children, className = "", collapsible = false, defaultOpen = true }: {
  title: string; sub?: string; right?: React.ReactNode; children: React.ReactNode; className?: string;
  collapsible?: boolean; defaultOpen?: boolean;
  /** Header controls shown even while the card is collapsed (right only shows open). */
  pinned?: React.ReactNode;
}) {
  // Collapsible cards remember open/closed per browser.
  const key = `ebDialerCard:${title}`;
  const [open, setOpen] = useState<boolean>(() => {
    if (!collapsible) return true;
    try {
      const v = localStorage.getItem(key);
      return v == null ? defaultOpen : v === "1";
    } catch {
      return defaultOpen;
    }
  });
  const toggle = () => {
    setOpen((o) => {
      try { localStorage.setItem(key, o ? "0" : "1"); } catch { /* private mode */ }
      return !o;
    });
  };
  const heading = (
    <div>
      <h2 className="text-base font-bold text-ink">{title}</h2>
      {sub && <p className="mt-0.5 text-xs text-ink-muted">{sub}</p>}
    </div>
  );
  return (
    <section className={`glass rounded-2xl p-5 ${className}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        {collapsible ? (
          <button type="button" onClick={toggle} aria-expanded={open}
            className="-m-1 flex items-start gap-2 rounded-lg p-1 text-left hover:bg-black/5">
            <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden
              className={`mt-0.5 h-5 w-5 flex-shrink-0 text-ink-muted transition-transform ${open ? "rotate-90" : ""}`}>
              <path fillRule="evenodd" d="M7.2 14.8a.75.75 0 0 1 0-1.06L10.94 10 7.2 6.26a.75.75 0 1 1 1.06-1.06l4.27 4.27a.75.75 0 0 1 0 1.06l-4.27 4.27a.75.75 0 0 1-1.06 0Z" clipRule="evenodd" />
            </svg>
            {heading}
          </button>
        ) : heading}
        {(pinned || (open && right)) && (
          <div className="flex flex-wrap items-center gap-2">
            {pinned}
            {open && right}
          </div>
        )}
      </div>
      {open && <div className="mt-4">{children}</div>}
    </section>
  );
}

function Tile({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-hairline-soft bg-white/60 px-4 py-3">
      <div className="text-xs font-medium text-ink-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-ink">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink-faint">{sub}</div>}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-xl border border-dashed border-hairline px-4 py-6 text-center text-sm text-ink-faint">{children}</div>;
}

function Alert({ tone, title, children }: { tone: "danger" | "pending" | "info"; title: string; children: React.ReactNode }) {
  const cls = {
    danger: "border-danger/30 bg-danger/8 text-danger",
    pending: "border-pending/30 bg-pending/10 text-pending",
    info: "border-accent/25 bg-accent/8 text-ink",
  }[tone];
  const icon = tone === "danger" ? "⚠" : tone === "pending" ? "●" : "ℹ";
  return (
    <div className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs ${cls}`}>
      <span aria-hidden className="font-bold">{icon}</span>
      <span className="font-semibold">{title}</span>
      <span className="hidden text-ink-muted sm:inline">· {children}</span>
    </div>
  );
}

const AGENT_STATE: Record<AgentRow["state"], { label: string; tone: string; dot: string }> = {
  on_call: { label: "On a call", tone: "text-success", dot: "bg-success" },
  waiting: { label: "Waiting on the line", tone: "text-ink-soft", dot: "bg-accent" },
  busy: { label: "Working another lead", tone: "text-ink-muted", dot: "bg-pending" },
  no_line: { label: "No headset line", tone: "text-danger", dot: "bg-danger" },
  pool_only: { label: "Regular pool only", tone: "text-ink-muted", dot: "bg-black/25" },
  break: { label: "On break", tone: "text-ink-faint", dot: "bg-black/20" },
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-ink-muted">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

const inputCls = "w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent disabled:opacity-60";

function NumberInput({ value, onChange, min, max, step = 1, suffix, disabled }: {
  value: number; onChange: (v: number) => void; min: number; max: number; step?: number; suffix?: string; disabled?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <input type="number" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        // The server clamps to the same range; snap here so what you see is what is saved.
        onBlur={() => { const v = Math.min(max, Math.max(min, Math.round(value || min))); if (v !== value) onChange(v); }}
        className={`${inputCls} max-w-[7rem]`} />
      {suffix && <span className="text-sm text-ink-muted">{suffix}</span>}
      <span className="text-[11px] text-ink-faint">{min}–{max}</span>
    </div>
  );
}

/** Play a call recording inline. <audio> can't send headers, so the token
    rides in the URL (same as training videos). */
function RecordingPlayer({ attemptId, status }: { attemptId: string; status: string | null }) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  if (status === "pending") return <span className="text-xs text-ink-faint">Saving…</span>;
  if (status === "failed") return <span className="text-xs text-danger">Not saved</span>;
  if (status !== "stored") return <span className="text-xs text-ink-faint">—</span>;
  if (!open) {
    return (
      <button onClick={() => setOpen(true)} title="Play recording"
        className="inline-flex items-center gap-1.5 rounded-full border border-hairline bg-white py-1 pl-1 pr-3 text-xs font-semibold text-ink hover:border-accent hover:text-accent">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent text-white">
          <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><path d="M7 4.5v15l12-7.5z" /></svg>
        </span>
        Recording
      </button>
    );
  }
  const src = `/api/v1/voice/dialer/recordings/${attemptId}?token=${encodeURIComponent(getAccessToken() || "")}`;
  if (failed) {
    return (
      <a href={src} target="_blank" rel="noopener" className="text-xs font-semibold text-accent underline">
        Open recording
      </a>
    );
  }
  return <audio controls autoPlay src={src} onError={() => setFailed(true)} className="h-8 w-56 max-w-full" />;
}

type Convo = {
  lead_id: string;
  customer_name: string | null;
  phone_number: string;
  last_message: string | null;
  last_at: string | null;
  texts: number;
  stage: string;
  last_call_status: string | null;
};
type ThreadItem =
  | { kind: "text"; at: string | null; direction: string; body: string; consent?: "opted_out" | "opted_in" | null }
  | { kind: "call"; at: string | null; attempt_id: string; status: string; disposition: string | null; direction: string; agent_name: string | null; duration_seconds: number | null; has_recording: boolean };
type Thread = { lead_id: string; customer_name: string | null; phone_number: string; state: string | null; stage: string; disposition: string | null; items: ThreadItem[] };

// ---- Opted out: the Do-Not-Call list ---------------------------------------

type OptOut = {
  id: string;
  phone_number: string;
  reason: string | null;
  created_at: string | null;
  customer_name: string | null;
  last_text: string | null;
};
type OptOutPage = { items: OptOut[]; has_more: boolean; counts: Record<string, number> };

const OPT_REASON: Record<string, { label: string; tone: string }> = {
  OPTED_OUT: { label: "Texted STOP", tone: "bg-danger/10 text-danger" },
  NOT_INTERESTED: { label: "Not interested", tone: "bg-pending/12 text-pending" },
  WRONG_NUMBER: { label: "Wrong number", tone: "bg-black/5 text-ink-muted" },
  ABUSIVE: { label: "Rude / abusive", tone: "bg-danger/10 text-danger" },
  UNQUALIFIED: { label: "Unqualified", tone: "bg-black/5 text-ink-muted" },
  MANUAL: { label: "Blocked by admin", tone: "bg-accent/10 text-ink-soft" },
};
const optReason = (r: string | null) =>
  OPT_REASON[r || ""] || { label: r ? r.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()) : "Other", tone: "bg-black/5 text-ink-muted" };

/* Who is never called or texted again, and why. Follows the page's period;
   admins can block a number by hand or take one off after a mistake (a real
   STOP asks twice — they may only be contacted again after opting back in). */
function OptOutPanel({ periodQs, periodLabel, canEdit }: { periodQs: string; periodLabel: string; canEdit: boolean }) {
  const [kind, setKind] = useState<"all" | "stop" | "not_interested" | "abusive" | "wrong_number" | "other">("all");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<OptOut[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [phone, setPhone] = useState("");
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const rowsRef = useRef<OptOut[]>([]);
  const listRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const qs = `kind=${kind}&${periodQs}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`;

  const load = useCallback(async () => {
    try {
      const r = await api<OptOutPage>(`/voice/dialer/optouts?${qs}&limit=${Math.max(30, rowsRef.current.length)}`);
      rowsRef.current = r.items;
      setRows(r.items);
      setMore(r.has_more);
      setCounts(r.counts);
    } catch {
      /* transient */
    }
  }, [qs]);

  useEffect(() => {
    rowsRef.current = [];
    const t = setTimeout(load, q ? 250 : 0); // small debounce while typing
    const id = setInterval(load, 15000);
    return () => {
      clearTimeout(t);
      clearInterval(id);
    };
  }, [load, q]);

  const loadMore = useCallback(async () => {
    const last = rowsRef.current[rowsRef.current.length - 1];
    if (!last || loadingMore) return;
    setLoadingMore(true);
    try {
      const r = await api<OptOutPage>(`/voice/dialer/optouts?${qs}&limit=30&before=${encodeURIComponent(last.created_at || "")}`);
      const seen = new Set(rowsRef.current.map((x) => x.id));
      rowsRef.current = [...rowsRef.current, ...r.items.filter((x) => !seen.has(x.id))];
      setRows(rowsRef.current);
      setMore(r.has_more);
    } catch {
      /* transient */
    } finally {
      setLoadingMore(false);
    }
  }, [qs, loadingMore]);

  useEffect(() => {
    const el = endRef.current;
    if (!el || !more) return;
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) loadMore(); }, { root: listRef.current, rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [more, loadMore, rows.length]);

  async function block() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ already?: boolean }>("/voice/dialer/optouts", { method: "POST", body: JSON.stringify({ phone }) });
      setMsg({ text: r.already ? "Already on the list." : "Number blocked.", ok: true });
      setPhone("");
      await load();
    } catch (e) {
      const t = e instanceof Error ? e.message : "";
      setMsg({ text: /valid/.test(t) ? "Enter a valid US phone number." : "Could not block that number.", ok: false });
    } finally {
      setBusy(false);
    }
  }

  async function unblock(r: OptOut) {
    setBusy(true);
    try {
      await api(`/voice/dialer/optouts/${r.id}${r.reason === "OPTED_OUT" ? "?confirm_stop=true" : ""}`, { method: "DELETE" });
      setConfirm(null);
      setMsg({ text: `${formatPhone(r.phone_number)} removed — they can be contacted again.`, ok: true });
      await load();
    } catch {
      setMsg({ text: "Could not remove it.", ok: false });
    } finally {
      setBusy(false);
    }
  }

  const total = counts.all ?? 0;
  return (
    <Card
      collapsible
      defaultOpen={false}
      title="Opted out"
      sub={`${total} ${total === 1 ? "number" : "numbers"} never contacted again · ${periodLabel}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          {([
            ["all", "All"], ["stop", "Texted STOP"], ["not_interested", "Not interested"],
            ["abusive", "Rude / abusive"], ["wrong_number", "Wrong number"], ["other", "Other"],
          ] as [typeof kind, string][]).map(([k, l]) => (
            <button key={k} onClick={() => setKind(k)}
              className={`rounded-full px-2.5 py-1 text-xs font-semibold ${kind === k ? "bg-accent text-white" : "bg-black/5 text-ink-muted hover:bg-black/10"}`}>
              {l} · {counts[k] ?? 0}
            </button>
          ))}
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or number"
          className="w-full rounded-lg border border-hairline bg-white px-3 py-1.5 text-sm text-ink sm:w-56" />
      </div>

      {canEdit && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="(305) 555-0123"
            onKeyDown={(e) => e.key === "Enter" && phone.trim() && block()}
            className="w-44 rounded-lg border border-hairline bg-white px-3 py-1.5 text-sm text-ink" />
          <button onClick={block} disabled={busy || !phone.trim()}
            className="rounded-lg border border-danger/30 bg-danger/8 px-3 py-1.5 text-sm font-semibold text-danger hover:bg-danger/15 disabled:opacity-50">
            Block number
          </button>
          {msg && <span className={`text-xs font-semibold ${msg.ok ? "text-success" : "text-danger"}`}>{msg.text}</span>}
        </div>
      )}

      {rows.length === 0 ? (
        <div className="mt-3"><Empty>{q ? "No match." : "Nobody opted out in this period."}</Empty></div>
      ) : (
        <div ref={listRef} className="mt-3 max-h-[28rem] overflow-y-auto pr-1">
          <div className={`hidden gap-4 border-b border-hairline px-3 pb-2 text-xs font-semibold text-ink-faint md:grid ${canEdit ? "md:grid-cols-[7rem_minmax(0,1.3fr)_9rem_minmax(0,1.6fr)_11rem]" : "md:grid-cols-[7rem_minmax(0,1.3fr)_9rem_minmax(0,1.6fr)]"}`}>
            <span>When</span><span>Client</span><span>Reason</span><span>What they said</span>{canEdit && <span className="text-right">Action</span>}
          </div>
          <ul className="divide-y divide-hairline-soft">
            {rows.map((r) => {
              const why = optReason(r.reason);
              const asking = confirm === r.id;
              return (
                <li key={r.id}
                  className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 px-3 py-3 hover:bg-black/[0.03] ${canEdit ? "md:grid-cols-[7rem_minmax(0,1.3fr)_9rem_minmax(0,1.6fr)_11rem]" : "md:grid-cols-[7rem_minmax(0,1.3fr)_9rem_minmax(0,1.6fr)]"}`}>
                  <span className="order-3 text-xs tabular-nums text-ink-faint md:order-none">{when(r.created_at)}</span>
                  <div className="order-1 min-w-0 md:order-none">
                    <div className="truncate text-sm font-semibold text-ink">{r.customer_name || "Unknown"}</div>
                    <div className="text-xs tabular-nums text-ink-faint">{formatPhone(r.phone_number)}</div>
                  </div>
                  <span className={`order-2 justify-self-end rounded-full px-2.5 py-1 text-xs font-bold md:order-none md:justify-self-start ${why.tone}`}>{why.label}</span>
                  <span className="order-4 col-span-2 truncate text-sm text-ink-soft md:order-none md:col-span-1" title={r.last_text || undefined}>
                    {r.last_text ? `“${r.last_text}”` : <span className="text-ink-faint">—</span>}
                  </span>
                  {canEdit && (
                    <div className="order-5 col-span-2 md:order-none md:col-span-1 md:text-right">
                      {asking ? (
                        <div className="flex flex-col items-start gap-1 md:items-end">
                          {r.reason === "OPTED_OUT" && (
                            <span className="text-[11px] font-semibold text-danger">They texted STOP. Remove only if they asked to hear from us again.</span>
                          )}
                          <div className="flex gap-1.5">
                            <button onClick={() => unblock(r)} disabled={busy}
                              className="rounded-lg bg-danger px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50">Yes, remove</button>
                            <button onClick={() => setConfirm(null)}
                              className="rounded-lg border border-hairline px-2.5 py-1 text-xs font-semibold text-ink-muted hover:text-ink">Keep</button>
                          </div>
                        </div>
                      ) : (
                        <button onClick={() => setConfirm(r.id)}
                          className="rounded-lg border border-hairline px-2.5 py-1 text-xs font-semibold text-ink-muted hover:border-danger/40 hover:text-danger">
                          Remove from list
                        </button>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <div ref={endRef} className="py-2 text-center text-xs text-ink-faint">{loadingMore ? "Loading more…" : ""}</div>
        </div>
      )}
    </Card>
  );
}

type OwnerRow = {
  phone_number: string;
  customer_name: string | null;
  owner_id: string;
  owner_name: string | null;
  source: "call" | "admin";
  set_by_name: string | null;
  updated_at: string | null;
};
type OwnerPage = { items: OwnerRow[]; more: boolean; agents: { id: string; name: string }[] };

/** Client owners — whose callbacks go to whom. Admins reassign here; the next
    agent who calls the client takes them back over (most recent wins). */
function ClientOwnersPanel({ canEdit }: { canEdit: boolean }) {
  const [data, setData] = useState<OwnerPage | null>(null);
  const [q, setQ] = useState("");
  const [who, setWho] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [newPhone, setNewPhone] = useState("");
  const [newAgent, setNewAgent] = useState("");

  const load = useCallback(async (more = false) => {
    const qs = new URLSearchParams({ limit: "30" });
    if (q.trim()) qs.set("q", q.trim());
    if (who) qs.set("agent", who);
    if (more && data?.items.length) qs.set("before", data.items[data.items.length - 1].updated_at || "");
    try {
      const page = await api<OwnerPage>(`/voice/dialer/owners?${qs}`);
      setData((d) => (more && d ? { ...page, items: [...d.items, ...page.items] } : page));
      setErr("");
    } catch {
      setErr("Couldn't load client owners.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, who, data?.items.length]);

  useEffect(() => {
    const t = setTimeout(() => load(false), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, who]);

  async function assign(phone: string, userId: string) {
    setBusy(true);
    try {
      await api("/voice/dialer/owners", { method: "PUT", body: JSON.stringify({ phone, user_id: userId }) });
      setNewPhone("");
      setNewAgent("");
      await load(false);
    } catch (e) {
      const m = e instanceof Error ? e.message : "";
      setErr(/"detail":"([^"]+)"/.exec(m)?.[1] || "Couldn't change the owner.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(phone: string) {
    setBusy(true);
    try {
      await api(`/voice/dialer/owners?phone=${encodeURIComponent(phone)}`, { method: "DELETE" });
      await load(false);
    } finally {
      setBusy(false);
    }
  }

  const agents = data?.agents || [];
  return (
    <Card collapsible defaultOpen={false} title="Client owners"
      sub="Whose callbacks go to whom: the agent who last called the client, unless an admin changed it.">
      <div className="flex flex-wrap items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or phone"
          className={`${inputCls} max-w-xs`} aria-label="Search client owners" />
        <select value={who} onChange={(e) => setWho(e.target.value)} className={`${inputCls} max-w-[14rem]`} aria-label="Filter by agent">
          <option value="">All agents</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </div>
      {canEdit && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl bg-black/5 p-3">
          <span className="text-xs font-semibold text-ink-muted">Assign a client:</span>
          <input value={newPhone} onChange={(e) => setNewPhone(e.target.value)} placeholder="(555) 123-4567" inputMode="tel"
            className={`${inputCls} max-w-[11rem]`} aria-label="Client phone" />
          <select value={newAgent} onChange={(e) => setNewAgent(e.target.value)} className={`${inputCls} max-w-[14rem]`} aria-label="Agent">
            <option value="">Pick an agent</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <button onClick={() => assign(newPhone, newAgent)} disabled={busy || !newAgent || newPhone.replace(/\D/g, "").length < 10}
            className="rounded-lg bg-accent px-3 py-2 text-sm font-bold text-white hover:bg-accent-hover disabled:opacity-40">
            Assign
          </button>
        </div>
      )}
      {err && <div className="mt-3 text-sm text-danger">{err}</div>}
      {data && data.items.length === 0 ? (
        <div className="mt-3"><Empty>{q || who ? "No match." : "No owners yet — a client gets one the first time an agent calls or talks to them."}</Empty></div>
      ) : (
        <ul className="mt-3 divide-y divide-hairline-soft">
          {data?.items.map((r) => (
            <li key={r.phone_number} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2.5">
              <div className="min-w-[12rem] flex-1">
                <div className="text-sm font-semibold text-ink">{r.customer_name || "Unknown"}</div>
                <div className="text-xs tabular-nums text-ink-muted">{formatPhone(r.phone_number)}</div>
              </div>
              <div className="min-w-[10rem] text-xs text-ink-faint">
                {r.source === "admin" ? <>Set by {r.set_by_name || "an admin"}</> : "From their last call"} · {when(r.updated_at)}
              </div>
              {canEdit ? (
                <div className="flex items-center gap-2">
                  <select value={r.owner_id} disabled={busy} onChange={(e) => assign(r.phone_number, e.target.value)}
                    className={`${inputCls} w-48`} aria-label={`Owner of ${r.phone_number}`}>
                    {!agents.some((a) => a.id === r.owner_id) && <option value={r.owner_id}>{r.owner_name || "—"}</option>}
                    {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select>
                  <button onClick={() => remove(r.phone_number)} disabled={busy} title="Remove owner — callbacks go to any free agent"
                    className="rounded-lg border border-hairline px-2.5 py-2 text-xs font-semibold text-ink-muted hover:bg-black/5 disabled:opacity-40">
                    Remove
                  </button>
                </div>
              ) : (
                <span className="text-sm font-semibold text-ink-soft">{r.owner_name}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {data?.more && (
        <button onClick={() => load(true)} className="mt-2 w-full rounded-lg border border-hairline py-2 text-sm font-semibold text-ink-muted hover:bg-black/5">
          Load more
        </button>
      )}
    </Card>
  );
}

function InboxPanel() {
  const [list, setList] = useState<Convo[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [q, setQ] = useState("");

  const loadList = useCallback(async () => {
    try {
      const r = await api<{ items: Convo[] }>("/voice/dialer/inbox");
      setList(r.items);
      setSel((cur) => (cur && r.items.some((c) => c.lead_id === cur) ? cur : r.items[0]?.lead_id ?? null));
    } catch {
      /* transient */
    }
  }, []);
  const loadThread = useCallback(async () => {
    if (!sel) return setThread(null);
    try {
      setThread(await api<Thread>(`/voice/dialer/inbox/${sel}`));
    } catch {
      setThread(null);
    }
  }, [sel]);

  useEffect(() => {
    loadList();
    const id = setInterval(loadList, 6000);
    const s = getSocket();
    const nudge = () => loadList();
    const evts = ["sms:new_message", "voice:dialer_updated"];
    evts.forEach((e) => s.on(e, nudge));
    return () => {
      clearInterval(id);
      evts.forEach((e) => s.off(e, nudge));
    };
  }, [loadList]);
  useEffect(() => {
    loadThread();
    const id = setInterval(loadThread, 6000);
    return () => clearInterval(id);
  }, [loadThread]);

  const shown = (list || []).filter((c) => {
    const k = q.trim().toLowerCase();
    if (!k) return true;
    return (c.customer_name || "").toLowerCase().includes(k) || c.phone_number.replace(/\D/g, "").includes(k.replace(/\D/g, "") || "~");
  });

  return (
    <Card collapsible defaultOpen title="Inbox" sub={list ? `${list.length} clients texted back` : undefined}>
      {list && list.length === 0 ? (
        <Empty>No texts yet. Replies to our Vonage numbers appear here.</Empty>
      ) : (
        <div className="grid overflow-hidden rounded-xl border border-hairline-soft md:grid-cols-[19rem_1fr]" style={{ height: "30rem" }}>
          {/* list */}
          <div className="flex min-h-0 flex-col border-b border-hairline-soft md:border-b-0 md:border-r">
            <div className="p-2">
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or number" className={inputCls} />
            </div>
            <ul className="min-h-0 flex-1 overflow-y-auto">
              {shown.map((c) => (
                <li key={c.lead_id}>
                  <button onClick={() => setSel(c.lead_id)}
                    className={`block w-full px-3 py-2.5 text-left ${sel === c.lead_id ? "bg-accent/10" : "hover:bg-black/5"}`}>
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-ink">{c.customer_name || formatPhone(c.phone_number)}</span>
                      <span className="flex-shrink-0 text-[11px] text-ink-faint">{when(c.last_at)}</span>
                    </div>
                    <div className="mt-0.5 flex items-center justify-between gap-2">
                      <span className="truncate text-xs text-ink-muted">{c.last_message || "—"}</span>
                      <span className="flex-shrink-0 rounded-full bg-black/5 px-1.5 py-0.5 text-[10px] font-semibold text-ink-muted">{c.stage}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          {/* thread */}
          <div className="flex min-h-0 flex-col">
            {!thread ? (
              <div className="m-auto text-sm text-ink-faint">Pick a conversation</div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2 border-b border-hairline-soft px-4 py-3">
                  <span className="font-semibold text-ink">{thread.customer_name || "Unknown"}</span>
                  <span className="text-sm tabular-nums text-ink-muted">{formatPhone(thread.phone_number)}</span>
                  {thread.state && <span className="text-sm text-ink-faint">· {thread.state}</span>}
                  <span className="ml-auto rounded-full bg-black/5 px-2 py-0.5 text-xs font-semibold text-ink-muted">
                    {thread.stage}{thread.disposition ? ` · ${thread.disposition.replace(/_/g, " ").toLowerCase()}` : ""}
                  </span>
                </div>
                <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-4 py-3">
                  {thread.items.map((it, i) =>
                    it.kind === "text" ? (
                      <div key={i} className={`flex ${it.direction === "OUTBOUND" ? "justify-end" : "justify-start"}`}>
                        <div className={`max-w-[75%] rounded-2xl px-3 py-2 text-sm ${it.direction === "OUTBOUND" ? "bg-accent text-white" : "border border-hairline-soft bg-white text-ink"}`}>
                          {it.body}
                          <div className={`mt-0.5 text-[10px] ${it.direction === "OUTBOUND" ? "text-white/70" : "text-ink-faint"}`}>{when(it.at)}</div>
                          {it.consent && (
                            <div className={`mt-1 inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${it.consent === "opted_in" ? "bg-success/15 text-success" : "bg-danger/10 text-danger"}`}>
                              {it.consent === "opted_in" ? "✓ Opted back in by text" : "Opted out"}
                            </div>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div key={i} className="flex flex-wrap items-center justify-center gap-2 py-1 text-xs text-ink-muted">
                        <span>📞 {it.direction === "inbound" ? "They called us" : "We called"} · {when(it.at)}</span>
                        <span className={`rounded-full px-2 py-0.5 font-semibold ${outcome(it).tone}`}>{outcome(it).label}</span>
                        {it.agent_name && <span>with {it.agent_name}</span>}
                        {it.duration_seconds != null && <span className="tabular-nums">{mmss(it.duration_seconds)}</span>}
                        {it.has_recording && <RecordingPlayer attemptId={it.attempt_id} status="stored" />}
                      </div>
                    ),
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

// ---- page -----------------------------------------------------------------

type Tab = "all" | "agent" | "retry" | "calling_hours" | "dialing";

export default function AutoDialer() {
  const canEdit = isAdminClass();
  const [ov, setOv] = useState<Overview | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);
  const [tab, setTab] = useState<Tab>("all");
  const [view, setView] = useState<"waiting" | "history">("waiting");
  const [hist, setHist] = useState<"all" | "talked" | "sales" | "missed">("all");
  // One period for the whole page: the stats card and the Called list.
  const [period, setPeriod] = useState<Period>("today");
  const [dayFrom, setDayFrom] = useState(isoDay());
  const [dayTo, setDayTo] = useState(isoDay());
  const periodQs = period === "custom"
    ? `period=custom&from=${dayFrom}&to=${dayTo}`
    : `period=${period}`;
  const [simPhone, setSimPhone] = useState("(305) 555-0101");
  const [simText, setSimText] = useState("Yes");
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  const [, tick] = useState(0);
  // Called list: paged from /voice/dialer/calls, more loaded on scroll.
  const [calls, setCalls] = useState<Attempt[]>([]);
  const [callCounts, setCallCounts] = useState<Record<string, number>>({});
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const callsRef = useRef<Attempt[]>([]);
  const listRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      const o = await api<Overview>(`/voice/dialer/overview?${periodQs}`);
      setOv(o);
      setDraft((d) => (d && dirty ? d : o.settings));
    } catch {
      /* transient */
    }
  }, [dirty, periodQs]);

  useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    const s = getSocket();
    const nudge = () => load();
    const evts = ["voice:dialer_updated", "sms:queue_updated"];
    evts.forEach((e) => s.on(e, nudge));
    return () => {
      clearInterval(id);
      evts.forEach((e) => s.off(e, nudge));
    };
  }, [load]);

  // First page (or every loaded row, on refresh) — keeps statuses fresh without
  // throwing away what the manager already scrolled through.
  const refreshCalls = useCallback(async (reset = false) => {
    const n = reset ? CALLS_PAGE : Math.max(CALLS_PAGE, callsRef.current.length);
    try {
      const r = await api<CallPage>(`/voice/dialer/calls?kind=${hist}&limit=${n}&${periodQs}`);
      callsRef.current = r.items;
      setCalls(r.items);
      setHasMore(r.has_more);
      setCallCounts(r.counts);
    } catch {
      /* transient */
    }
  }, [hist, periodQs]);

  const loadMore = useCallback(async () => {
    const last = callsRef.current[callsRef.current.length - 1];
    if (!last || loadingMore) return;
    setLoadingMore(true);
    try {
      const r = await api<CallPage>(
        `/voice/dialer/calls?kind=${hist}&limit=${CALLS_PAGE}&${periodQs}&before=${encodeURIComponent(last.created_at || "")}`,
      );
      const seen = new Set(callsRef.current.map((a) => a.id));
      callsRef.current = [...callsRef.current, ...r.items.filter((a) => !seen.has(a.id))];
      setCalls(callsRef.current);
      setHasMore(r.has_more);
      setCallCounts(r.counts);
    } catch {
      /* transient */
    } finally {
      setLoadingMore(false);
    }
  }, [hist, periodQs, loadingMore]);

  useEffect(() => {
    callsRef.current = [];
    setCalls([]);
    setHasMore(false);
    refreshCalls(true);
    const id = setInterval(() => refreshCalls(), 5000);
    return () => clearInterval(id);
  }, [refreshCalls]);

  // Load the next page when the bottom of the list scrolls into view.
  useEffect(() => {
    const el = endRef.current;
    if (!el || !hasMore || view !== "history") return;
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) loadMore(); }, { root: listRef.current, rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, loadMore, view, calls.length]);

  useEffect(() => {
    const id = setInterval(() => tick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  async function save(patch: Partial<Settings>, isRules = false) {
    setBusy(true);
    setErr("");
    try {
      const s = await api<Settings>("/voice/dialer/settings", { method: "PUT", body: JSON.stringify(patch) });
      setDraft(s);
      if (isRules) {
        setDirty(false);
        setSaved(true);
        setTimeout(() => setSaved(false), 2500);
      }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  async function sim(path: string, body: object) {
    setBusy(true);
    setErr("");
    try {
      const r = await api<{ result?: string; status?: string }>(path, { method: "POST", body: JSON.stringify(body) });
      if (r.result) setNote(SIM_RESULT[r.result] || { text: r.result, ok: true });
      else if (r.status) setNote({ text: `Call result: ${RESULT[r.status]?.label || r.status}`, ok: true });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Simulator call failed");
    } finally {
      setBusy(false);
    }
  }

  if (!ov || !draft) {
    return <div className="py-20 text-center text-sm text-ink-faint">Loading the auto dialer…</div>;
  }

  const s = ov.settings;
  const t = ov.today;
  const edit = (patch: Partial<Settings>) => {
    setDraft({ ...draft, ...patch });
    setDirty(true);
  };
  const flying = ov.recent.filter((a) => IN_FLIGHT.includes(a.status));
  // Live = still ringing/being checked, or connected to an agent (on-call first).
  const holding = ov.recent.filter((a) => a.status === "HOLDING" || a.status === "BRIDGING");
  const live = [
    ...ov.recent.filter((a) => a.status === "CONNECTED"),
    ...holding,
    ...flying,
  ];
  const onCall = ov.agents.filter((a) => a.state === "on_call").length;
  const waitingAgents = ov.agents.filter((a) => a.state === "waiting").length;
  const readyCount = ov.ready.filter((r) => r.status === "READY_TO_CALL").length;
  // Newest reply on top (display only — the dialer still calls the longest-waiting first).
  const shownReady = ov.ready
    .filter((r) =>
      tab === "all" ? true : tab === "dialing" ? r.status === "DIALING" : r.status === "READY_TO_CALL" && r.waiting_for === tab,
    )
    .sort((a, b) => (b.yes_at || "").localeCompare(a.yes_at || ""));
  const overLimit = t.abandon_rate_pct >= ABANDON_LIMIT;
  const outcomes = [
    { label: "Talked to an agent", n: t.connected, help: "A live person answered and was connected" },
    { label: "Voicemail", n: t.machine, help: "Machine detected — hung up, called again later" },
    { label: "No answer or busy", n: t.no_answer, help: "Rang out or line busy — called again later" },
    { label: "No agent was free", n: t.abandoned, help: "Live person, but every agent was busy (abandoned call)" },
    { label: "Failed", n: t.failed, help: "The call could not be placed" },
  ];
  const maxOutcome = Math.max(1, ...outcomes.map((o) => o.n));
  const mode = ov.configured
    ? { label: "Vonage connected", tone: "bg-success/12 text-success" }
    : ov.simulate
      ? { label: "Simulator · no Vonage keys", tone: "bg-pending/12 text-pending" }
      : { label: "Vonage not configured", tone: "bg-danger/10 text-danger" };

  return (
    <div className="space-y-5">
      {/* ===== Header: title, what needs attention, on/off — one row ===== */}
      <div className="glass flex flex-wrap items-center gap-x-4 gap-y-3 rounded-2xl px-5 py-4">
        <h1 className="text-2xl font-bold tracking-tight text-ink" title="Calls clients who text YES and connects live answers to a free agent.">
          Auto Dialer
        </h1>
        {!canEdit && (
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${s.enabled ? "bg-success/12 text-success" : "bg-black/5 text-ink-muted"}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${s.enabled ? "animate-pulse bg-success" : "bg-black/30"}`} />
            {s.enabled ? "On" : "Off"}
          </span>
        )}
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${mode.tone}`}>{mode.label}</span>
        {err && <Alert tone="danger" title="Something went wrong">{err}</Alert>}
        {!s.enabled && <Alert tone="pending" title="Dialer is off">YES replies go to the regular pool.</Alert>}
        {s.enabled && ov.agents_with_line === 0 && (
          <Alert tone="pending" title="No agent on the line">Agents turn on calls in Lead Manager.</Alert>
        )}
        {overLimit && period === "today" && (
          <Alert tone="danger" title={`Abandoned ${t.abandon_rate_pct}% today`}>Over the {ABANDON_LIMIT}% limit.</Alert>
        )}
        {s.enabled && !ov.hours_open.eastern && (
          <Alert tone="info" title="Outside calling hours">
            {hourLabel(s.window_start_hour)} – {hourLabel(s.window_end_hour)}, client's time.
          </Alert>
        )}
        <DialButton className="ml-auto" />
        {canEdit && (
          <label className={`flex items-center gap-3 ${busy || (!s.enabled && !ov.available) ? "cursor-not-allowed opacity-50" : "cursor-pointer"}`}>
            <span className={`text-sm font-semibold ${s.enabled ? "text-success" : "text-ink-muted"}`}>
              Dialer {s.enabled ? "on" : "off"}
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={s.enabled}
              aria-label="Auto dialer"
              onClick={() => save({ enabled: !s.enabled })}
              disabled={busy || (!s.enabled && !ov.available)}
              className={`relative h-7 w-12 flex-shrink-0 rounded-full transition-colors ${s.enabled ? "bg-success" : "bg-black/20"}`}
            >
              <span className={`absolute left-0.5 top-0.5 h-6 w-6 rounded-full bg-[#fff] shadow transition-transform ${s.enabled ? "translate-x-5" : ""}`} />
            </button>
          </label>
        )}
      </div>

      {/* ===== Stats for the chosen period (also filters the Called list) ===== */}
      <Card
        title={PERIODS.find(([k]) => k === period)?.[1] || "Today"}
        sub={PERIOD_SUB[period]}
        right={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <div className="flex flex-wrap gap-1 rounded-xl bg-black/5 p-1">
              {PERIODS.map(([k, l]) => (
                <button key={k} onClick={() => setPeriod(k)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${period === k ? "bg-white text-ink shadow-sm" : "text-ink-muted hover:text-ink"}`}>
                  {l}
                </button>
              ))}
            </div>
            {period === "custom" && (
              <div className="flex items-center gap-1.5 text-xs text-ink-muted">
                <input type="date" value={dayFrom} max={dayTo} onChange={(e) => e.target.value && setDayFrom(e.target.value)}
                  className="rounded-lg border border-hairline bg-white px-2 py-1.5 text-xs text-ink" />
                <span>to</span>
                <input type="date" value={dayTo} min={dayFrom} max={isoDay()} onChange={(e) => e.target.value && setDayTo(e.target.value)}
                  className="rounded-lg border border-hairline bg-white px-2 py-1.5 text-xs text-ink" />
              </div>
            )}
          </div>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[1.4fr_1fr_1fr_1fr_1.3fr]">
          <div className="rounded-xl bg-accent/8 px-5 py-4 sm:col-span-2 lg:col-span-1">
            <div className="text-xs font-semibold text-ink-muted">Connected to an agent</div>
            <div className="mt-1 text-5xl font-bold text-ink">{t.connected}</div>
            <div className="mt-1 text-xs text-ink-muted">
              of {t.live_answers} live {t.live_answers === 1 ? "answer" : "answers"} · <b className="text-success">{t.sales} {t.sales === 1 ? "sale" : "sales"}</b> · {t.appointments} appt</div>
            <div className="text-xs text-ink-faint">
              {t.dialed} calls made{t.opted_out ? ` · ${t.opted_out} opted out` : ""}
            </div>
          </div>
          <Tile label="Answer rate" value={`${pct(t.live_answers, t.dialed)}%`} />
          <Tile label="Avg talk time" value={mmss(t.avg_talk_seconds)} />
          <Tile
            label="Connect speed"
            value={secs(t.avg_detect_ms != null && t.avg_bridge_ms != null ? t.avg_detect_ms + t.avg_bridge_ms : null)}
            sub="target 2s"
          />
          <div className="rounded-xl border border-hairline-soft bg-white/60 px-4 py-3">
            <div className="flex items-center justify-between text-xs font-medium text-ink-muted">
              <span>Abandoned calls</span>
              {overLimit && <span className="font-semibold text-danger">⚠ Over limit</span>}
            </div>
            <div className={`mt-1 text-2xl font-semibold ${overLimit ? "text-danger" : "text-ink"}`}>{t.abandon_rate_pct}%</div>
            {/* Meter: 0–2× the limit, limit marked. */}
            <div className="relative mt-2 h-2 rounded-full bg-black/8" title={`${t.abandoned} of ${t.live_answers} live answers had no free agent`}>
              <div
                className={`h-2 rounded-full ${overLimit ? "bg-danger" : "bg-accent"}`}
                style={{ width: `${Math.min(100, (t.abandon_rate_pct / (ABANDON_LIMIT * 2)) * 100)}%` }}
              />
              <div className="absolute -top-1 h-4 w-0.5 bg-ink/50" style={{ left: "50%" }} />
            </div>
            <div className="mt-1 text-[11px] text-ink-faint">limit {ABANDON_LIMIT}% · {t.abandoned} of {t.live_answers}</div>
          </div>
        </div>
        <ul className="mt-5 grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-5">
          {outcomes.map((o) => (
            <li key={o.label} title={`${o.help}: ${o.n}`}>
              <div className="flex items-baseline justify-between text-sm">
                <span className="text-ink-soft">{o.label}</span>
                <span className="font-semibold text-ink">{o.n}</span>
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-black/5">
                <div className="h-1.5 rounded-full bg-accent" style={{ width: `${(o.n / maxOutcome) * 100}%`, minWidth: o.n ? 4 : 0 }} />
              </div>
            </li>
          ))}
        </ul>
      </Card>

      {/* ===== Right now ===== */}
      <div className="grid gap-5 xl:grid-cols-2">
        <Card
          title="Agents"
          sub={`${waitingAgents} waiting on the line · ${onCall} on a call`}
        >
          {ov.agents.length === 0 ? (
            <Empty>No agent is working right now.</Empty>
          ) : (
            <ul className="divide-y divide-hairline-soft">
              {ov.agents.map((a) => {
                const st = AGENT_STATE[a.state];
                return (
                  <li key={a.user_id} className="flex items-center gap-3 py-2.5">
                    <span className={`h-2.5 w-2.5 flex-shrink-0 rounded-full ${st.dot} ${a.state === "on_call" ? "animate-pulse" : ""}`} />
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">{a.name}</span>
                    {a.call ? (
                      <span className="truncate text-sm text-success">
                        with {a.call.customer_name || formatPhone(a.call.phone_number)}
                        <span className="ml-2 tabular-nums text-ink-muted">{clock(a.call.connected_at)}</span>
                      </span>
                    ) : (
                      <span className={`text-sm ${st.tone}`}>{st.label}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card title="Live calls" sub={live.length ? `${live.length} active` : undefined}>
          {live.length === 0 ? (
            <Empty>
              {!s.enabled
                ? "The dialer is off."
                : readyCount === 0
                  ? "Nobody is waiting to be called."
                  : ov.queue.agent === 0
                    ? `Nobody is due right now — ${ov.queue.retry ? `${ov.queue.retry} waiting for a retry time` : ""}${ov.queue.retry && ov.queue.calling_hours ? ", " : ""}${ov.queue.calling_hours ? `${ov.queue.calling_hours} outside their calling hours` : ""}.`
                    : ov.ready_agents === 0
                      ? "Waiting for a free agent on the line."
                      : "Starting the next call…"}
            </Empty>
          ) : (
            <ul className="space-y-2">
              {live.map((a) => (
                <li key={a.id} className="rounded-xl border border-hairline-soft bg-white/60 px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-ink">{a.customer_name || "Unknown"}</span>
                    <span className="text-sm tabular-nums text-ink-muted">{formatPhone(a.phone_number)}</span>
                    <span className={`ml-auto rounded-full px-2 py-0.5 text-xs font-semibold ${RESULT[a.status]?.tone}`}>{RESULT[a.status]?.label}</span>
                  </div>
                  <div className="mt-0.5 text-xs text-ink-faint">
                    {a.status === "CONNECTED"
                      ? <>with <span className="font-semibold text-ink-soft">{a.agent_name || "agent"}</span> · {clock(a.connected_at)}</>
                      : a.status === "HOLDING" || a.status === "BRIDGING"
                        ? <>called in · waiting for <span className="font-semibold text-ink-soft">{a.hold_for_name || "the next free agent"}</span> · {clock(a.created_at)} on hold</>
                        : <>from {formatPhone(a.from_number)} · {clock(a.created_at)}</>}
                  </div>
                  {ov.simulate && ov.sim_mode === "manual" && a.status !== "CONNECTED" && (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span className="text-xs font-semibold text-pending">Simulate:</span>
                      {[["human", "Live person"], ["machine", "Voicemail"], ["no_answer", "No answer"], ["busy", "Busy"]].map(([r, l]) => (
                        <button key={r} onClick={() => sim(`/voice/dialer/sim/call/${a.id}`, { result: r })} disabled={busy}
                          className="rounded-lg border border-hairline bg-white px-2.5 py-1 text-xs font-semibold text-ink hover:bg-black/5 disabled:opacity-50">
                          {l}
                        </button>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* ===== Simulator (dev only) ===== */}
      {ov.simulate && (
        <section className="rounded-2xl border-2 border-dashed border-pending/40 bg-pending/5 p-5">
          <h2 className="text-base font-bold text-pending">Test it: simulate a text</h2>
          <p className="mt-1 text-sm text-ink-muted">Use a new number each time.</p>
          <label className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold text-ink">When the dialer calls:</span>
            <select
              value={ov.sim_mode || "human"}
              disabled={busy}
              onChange={async (e) => {
                await api("/voice/dialer/sim/mode", { method: "PUT", body: JSON.stringify({ mode: e.target.value }) });
                await load();
              }}
              className={`${inputCls} max-w-[16rem]`}
            >
              <option value="human">A live person answers (after 2 s)</option>
              <option value="machine">Voicemail picks up</option>
              <option value="no_answer">Nobody answers</option>
              <option value="busy">Line is busy</option>
              <option value="manual">Let me choose for each call</option>
            </select>
            {ov.sim_mode === "manual" && (
              <span className="text-xs text-ink-faint">Pick the result under <b>Live calls</b> above.</span>
            )}
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={!!ov.sim_ignore_hours} disabled={busy}
              onChange={async (e) => {
                await api("/voice/dialer/sim/ignore-hours", { method: "PUT", body: JSON.stringify({ on: e.target.checked }) });
                await load();
              }} />
            Ignore calling hours while testing
          </label>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input value={simPhone} onChange={(e) => setSimPhone(e.target.value)} placeholder="Phone" className={`${inputCls} max-w-[12rem]`} />
            <input value={simText} onChange={(e) => setSimText(e.target.value)} placeholder="Their text" className={`${inputCls} max-w-[12rem]`} />
            <button onClick={() => sim("/voice/dialer/sim/inbound", { phone: simPhone, text: simText })} disabled={busy}
              className="rounded-lg bg-pending px-4 py-2 text-sm font-bold text-white disabled:opacity-50">
              Send text
            </button>
          </div>
          {note && (
            <div className={`mt-3 rounded-lg px-3 py-2 text-sm font-medium ${note.ok ? "bg-success/12 text-success" : "bg-danger/10 text-danger"}`}>
              {note.text}
            </div>
          )}
        </section>
      )}

      {/* ===== Calls: waiting + history ===== */}
      <Card
        title="Calls"
        sub={view === "history" ? PERIODS.find(([k]) => k === period)?.[1] : undefined}
        right={
          <div className="flex rounded-xl bg-black/5 p-1">
            {([["waiting", `Waiting to call · ${ov.ready.length}`], ["history", `Called · ${callCounts.all ?? t.dialed}`]] as ["waiting" | "history", string][]).map(([k, l]) => (
              <button key={k} onClick={() => setView(k)}
                className={`rounded-lg px-3.5 py-1.5 text-sm font-semibold ${view === k ? "bg-white text-ink shadow-sm" : "text-ink-muted hover:text-ink"}`}>
                {l}
              </button>
            ))}
          </div>
        }
      >
        {view === "waiting" ? (
          <>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {([
            ["all", "All"],
            ["agent", `Ready now · ${ov.queue.agent}`],
            ["retry", `Retry later · ${ov.queue.retry}`],
            ["calling_hours", `Outside hours · ${ov.queue.calling_hours}`],
            ["dialing", `Dialing · ${ov.queue.dialing}`],
          ] as [Tab, string][]).map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`rounded-full px-2.5 py-1 text-xs font-semibold ${tab === k ? "bg-accent text-white" : "bg-black/5 text-ink-muted hover:bg-black/10"}`}>
              {l}
            </button>
          ))}
        </div>
        {shownReady.length === 0 ? (
          <Empty>Nobody here.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-faint">
                  <th className="py-2 pr-3 font-semibold">Client</th>
                  <th className="py-2 pr-3 font-semibold">Phone</th>
                  <th className="py-2 pr-3 font-semibold">State</th>
                  <th className="py-2 pr-3 font-semibold">Their reply</th>
                  <th className="py-2 pr-3 font-semibold">Replied</th>
                  <th className="py-2 pr-3 text-right font-semibold">Tries</th>
                  <th className="py-2 text-right font-semibold">Next call</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline-soft">
                {shownReady.map((r) => (
                  <tr key={r.id}>
                    <td className="py-2.5 pr-3 font-semibold text-ink">{r.customer_name || "Unknown"}</td>
                    <td className="py-2.5 pr-3 tabular-nums text-ink-muted">{formatPhone(r.phone_number)}</td>
                    <td className="py-2.5 pr-3 text-ink-muted">{r.state || "—"}</td>
                    <td className="max-w-[12rem] truncate py-2.5 pr-3 text-ink-muted">{r.yes_reply ? `“${r.yes_reply}”` : "—"}</td>
                    <td className="py-2.5 pr-3 text-ink-faint">{when(r.yes_at)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-ink-muted">{r.dial_attempts} / {s.max_total_attempts}</td>
                    <td className="py-2.5 text-right">
                      {r.status === "DIALING" ? (
                        <span className="rounded-full bg-pending/12 px-2 py-0.5 text-xs font-semibold text-pending">Dialing now</span>
                      ) : r.waiting_for === "retry" ? (
                        <span className="text-xs text-ink-muted">{when(r.next_dial_at)}</span>
                      ) : r.waiting_for === "calling_hours" ? (
                        <span className="text-xs text-ink-muted">When their hours open</span>
                      ) : (
                        <span className="rounded-full bg-success/12 px-2 py-0.5 text-xs font-semibold text-success">Next free agent</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
          </>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap gap-1.5">
              {([
                ["all", `All · ${callCounts.all ?? 0}`],
                ["talked", `Talked to agent · ${callCounts.talked ?? 0}`],
                ["sales", `Sales · ${callCounts.sales ?? 0}`],
                ["missed", `Missed · ${callCounts.missed ?? 0}`],
              ] as [typeof hist, string][]).map(([k, l]) => (
                <button key={k} onClick={() => { if (k !== hist) { callsRef.current = []; setCalls([]); setHasMore(false); setHist(k); } }}
                  className={`rounded-full px-2.5 py-1 text-xs font-semibold ${hist === k ? "bg-accent text-white" : "bg-black/5 text-ink-muted hover:bg-black/10"}`}>
                  {l}
                </button>
              ))}
            </div>
            {calls.length === 0 ? (
              <Empty>No calls here.</Empty>
            ) : (
              <div ref={listRef} className="max-h-[34rem] overflow-y-auto pr-1">
                <div className="hidden grid-cols-[7rem_minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.3fr)_10rem] gap-4 border-b border-hairline px-3 pb-2 text-xs font-semibold text-ink-faint md:grid">
                  <span>When</span><span>Client</span><span>Agent</span><span>Outcome</span><span className="text-right">Recording</span>
                </div>
                <ul className="divide-y divide-hairline-soft">
                  {calls.map((a) => {
                    const o = outcome(a);
                    return (
                      <li key={a.id} title={a.error || undefined}
                        className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 px-3 py-3 hover:bg-black/[0.03] md:grid-cols-[7rem_minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.3fr)_10rem]">
                        <span className="order-3 text-xs tabular-nums text-ink-faint md:order-none">{when(a.created_at)}</span>
                        <div className="order-1 min-w-0 md:order-none">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold text-ink">{a.customer_name || "Unknown"}</div>
                            <div className="text-xs tabular-nums text-ink-faint">
                              {formatPhone(a.phone_number)}{a.direction === "inbound" ? " · called us" : ""}
                            </div>
                          </div>
                        </div>
                        <span className="order-4 truncate text-sm text-ink-soft md:order-none">{a.agent_name || <span className="text-ink-faint">—</span>}</span>
                        <div className="order-2 flex flex-col items-end gap-0.5 md:order-none md:items-start">
                          <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${o.tone}`}>{o.label}</span>
                          {a.connected_at && a.duration_seconds != null && (
                            <span className="text-[11px] tabular-nums text-ink-faint">Talked {mmss(a.duration_seconds)}</span>
                          )}
                        </div>
                        <div className="order-5 md:order-none md:text-right">
                          {a.connected_at
                            ? <RecordingPlayer attemptId={a.id} status={a.status === "CONNECTED" ? null : a.recording || "pending"} />
                            : <span className="text-xs text-ink-faint">—</span>}
                        </div>
                      </li>
                    );
                  })}
                </ul>
                <div ref={endRef} className="py-3 text-center text-xs text-ink-faint">
                  {loadingMore ? "Loading more…" : hasMore ? "" : calls.length > CALLS_PAGE ? "That's every call" : ""}
                </div>
              </div>
            )}
          </>
        )}
      </Card>

      {/* ===== Inbox: their texts + our calls ===== */}
      <OptOutPanel periodQs={periodQs} periodLabel={PERIODS.find(([k]) => k === period)?.[1] || ""} canEdit={canEdit} />

      <InboxPanel />

      <ClientOwnersPanel canEdit={canEdit} />

      {/* ===== Rules ===== */}
      <Card
        collapsible
        defaultOpen={false}
        title="Dialing rules"
        sub={canEdit ? undefined : "View only"}
        pinned={canEdit && <DialerNotes s={s} />}
        right={canEdit && (
          <div className="flex items-center gap-2">
            {saved && <span className="text-sm font-semibold text-success">✓ Saved</span>}
            <button onClick={() => { setDraft(s); setDirty(false); }} disabled={!dirty || busy}
              className="rounded-lg border border-hairline px-3 py-2 text-sm font-semibold text-ink-muted hover:bg-black/5 disabled:opacity-40">
              Undo
            </button>
            <button onClick={() => { const { enabled: _e, ...rules } = draft; save(rules, true); }} disabled={!dirty || busy}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-bold text-white hover:bg-accent-hover disabled:opacity-40">
              Save
            </button>
          </div>
        )}
      >
        <div className="grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3 lg:grid-cols-4">
          <Field label="Call from (client time)">
            <NumberInput value={draft.window_start_hour} min={8} max={12} suffix=":00" disabled={!canEdit} onChange={(v) => edit({ window_start_hour: v })} />
          </Field>
          <Field label="Call until (client time)">
            <NumberInput value={draft.window_end_hour} min={12} max={20} suffix=":00" disabled={!canEdit} onChange={(v) => edit({ window_end_hour: v })} />
          </Field>
          <Field label="Ring time">
            <NumberInput value={draft.ring_seconds} min={15} max={45} suffix="sec" disabled={!canEdit} onChange={(v) => edit({ ring_seconds: v })} />
          </Field>
          <Field label="Retry after">
            <NumberInput value={draft.retry_minutes} min={5} max={1440} suffix="min" disabled={!canEdit} onChange={(v) => edit({ retry_minutes: v })} />
          </Field>
          <Field label="Wait before calling">
            <NumberInput value={draft.reply_delay_seconds} min={0} max={30} suffix="sec" disabled={!canEdit} onChange={(v) => edit({ reply_delay_seconds: v })} />
          </Field>
          <Field label="Max tries per day">
            <NumberInput value={draft.max_attempts_24h} min={1} max={3} disabled={!canEdit} onChange={(v) => edit({ max_attempts_24h: v })} />
          </Field>
          <Field label="Max tries total">
            <NumberInput value={draft.max_total_attempts} min={1} max={20} disabled={!canEdit} onChange={(v) => edit({ max_total_attempts: v })} />
          </Field>
          <Field label="Business name">
            <input value={draft.business_name} disabled={!canEdit} onChange={(e) => edit({ business_name: e.target.value })} placeholder="Insurance Alliance Group" className={inputCls} />
          </Field>
          <Field label="Callback number">
            <input value={draft.callback_number} disabled={!canEdit} onChange={(e) => edit({ callback_number: e.target.value })} placeholder="(561) 878-5803" className={inputCls} />
          </Field>
        </div>

        {/* Inbound: clients calling us (callbacks) */}
        <div className="mt-6 border-t border-hairline-soft pt-5">
          <h3 className="text-sm font-bold text-ink">When clients call us</h3>
          <p className="mt-0.5 text-xs text-ink-muted">Hold limit and open hours for callbacks. Outside open hours callers hear: "we are closed, our normal hours are {draft.hours_label}".</p>
          <div className="mt-4 grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3 lg:grid-cols-4">
            <Field label="Longest hold">
              <NumberInput value={Math.round(draft.max_hold_seconds / 60)} min={1} max={10} suffix="min" disabled={!canEdit}
                onChange={(v) => edit({ max_hold_seconds: v * 60 })} />
            </Field>
            <Field label="Open hours">
              <label className={`flex h-[38px] items-center gap-2 text-sm ${canEdit ? "cursor-pointer" : "opacity-60"}`}>
                <input type="checkbox" checked={draft.hours_enabled} disabled={!canEdit}
                  onChange={(e) => edit({ hours_enabled: e.target.checked })} className="h-4 w-4 accent-[var(--accent)]" />
                {draft.hours_enabled ? "On — closed message outside them" : "Off — take calls any time"}
              </label>
            </Field>
            <Field label="Opens">
              <input type="time" value={toHHMM(draft.open_start_minute)} disabled={!canEdit || !draft.hours_enabled}
                onChange={(e) => e.target.value && edit({ open_start_minute: fromHHMM(e.target.value) })} className={inputCls} />
            </Field>
            <Field label="Closes">
              <input type="time" value={toHHMM(draft.open_end_minute)} disabled={!canEdit || !draft.hours_enabled}
                onChange={(e) => e.target.value && edit({ open_end_minute: fromHHMM(e.target.value) })} className={inputCls} />
            </Field>
            <Field label="Time zone">
              <select value={draft.hours_tz} disabled={!canEdit || !draft.hours_enabled} onChange={(e) => edit({ hours_tz: e.target.value })} className={inputCls}>
                {TIMEZONES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
            <div className="col-span-2 sm:col-span-3 lg:col-span-3">
              <span className="mb-1.5 block text-xs font-semibold text-ink-muted">Open days</span>
              <div className="flex flex-wrap gap-1.5">
                {WEEKDAYS.map((d, i) => {
                  const on = draft.open_days.includes(i);
                  return (
                    <button key={d} type="button" disabled={!canEdit || !draft.hours_enabled}
                      onClick={() => edit({ open_days: on ? draft.open_days.filter((x) => x !== i) : [...draft.open_days, i].sort() })}
                      className={`h-9 w-12 rounded-lg border text-sm font-semibold transition disabled:opacity-50 ${on ? "border-accent bg-accent text-white" : "border-hairline bg-white text-ink-muted hover:bg-black/5"}`}>
                      {d}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}

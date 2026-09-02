import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { getSocket } from "../lib/socket";
import LeadsTools from "../components/LeadsTools";

type Agent = {
  user_id: string;
  name: string;
  status: string;
  activity?: string;
  wrapping?: boolean;
  total_leads_handled: number;
  break_reason?: string | null;
  break_started_at?: string | null;
};

function sinceMins(iso?: string | null): string {
  if (!iso) return "";
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "just now" : `${m}m`;
}
type Overview = {
  agents: Agent[];
  counts: {
    available: number; on_call: number; away: number; queued: number;
    talking: number; waiting: number; idle: number;
    yes_open: number; yes_today: number; wrapping: number;
  };
};
type Period = "day" | "week" | "month";
type PassKeepRow = { agent_name: string; kept: number; passed: number; offered: number; keep_rate_pct: number };
type AgentDisp = {
  agent_name: string;
  dispositions: Record<string, number>;
  breaks: Record<string, number>;
  break_seconds: number;
  break_count: number;
};
type YesLead = {
  id: string; phone_number: string; customer_name: string | null; last_message: string | null;
  disposition: string | null; agent_name: string; dispositioned_at: string | null;
};
type BreakRow = { agent_name: string; reason: string; started_at: string | null; ended_at: string | null; ongoing: boolean; duration_seconds: number };
type BreakTotal = { agent_name: string; total_seconds: number };
type DailyRow = {
  agent_name: string; shift_seconds: number; break_seconds: number; billable_seconds: number;
  applications: number; appts: number; avg_response_ms: number; conv_pct: number;
};
type QueuedLead = { id: string; phone_number: string; priority: string; last_message: string | null; created_at: string | null; last_message_at?: string | null };
type ActiveLead = {
  id: string; phone_number: string; status: string; priority: string;
  last_message: string | null; agent_name: string; message_count: number; accepted_at: string | null;
};
type Pool = { freshCount: number; agedCount: number; rejectedCount: number };
type LeaderRow = {
  agent_name: string; attempted: number; replied: number; reply_rate_pct: number;
  sold: number; appointments: number; conv_rate_pct: number; avg_response_ms: number | null;
};
type Funnel = { from: string; to: string; attempted: number; replied: number; sold: number; replied_pct: number; sold_pct: number };
type ParkedItem = { id: string; phone_number: string; last_message: string | null; agent_name: string; dispositioned_at: string | null };
type Activity = { agent_name: string; accepted: number; dispositioned: number };
const REFRESH_MS = 6_000;
const ACTIVITY_LABEL: Record<string, string> = { talking: "Talking", waiting: "Waiting to accept", idle: "Not talking", away: "On break", offline: "Offline", wrapping: "Wrapping" };
const ACTIVITY_DOT: Record<string, string> = { talking: "bg-accent", waiting: "bg-blue-500", idle: "bg-success", away: "bg-pending", offline: "bg-ink-faint", wrapping: "bg-purple-500" };
const PERIODS: Period[] = ["day", "week", "month"];

function fmtDuration(secs: number): string {
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}
function fmtHMS(secs: number): string {
  const s = Math.max(0, Math.floor(secs || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
}
function fmtClock(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function ago(iso: string | null): string {
  if (!iso) return "";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ${m % 60}m ago`;
}
const todayISO = () => new Date().toISOString().slice(0, 10);
const weekAgoISO = () => new Date(Date.now() - 6 * 864e5).toISOString().slice(0, 10);

type Tone = "danger" | "pending" | "success" | "accent" | "blue" | undefined;

function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-danger/30 border-t-danger align-middle" />
  );
}

// All panels share one neutral glass style matching the rest of the portal UI.
// `tone` is accepted (call sites still pass it) but intentionally ignored so the
// board reads as a single consistent surface rather than multi-colored panels.
function Panel({ title, right, children }: { title: string; right?: React.ReactNode; tone?: Tone; children: React.ReactNode }) {
  return (
    <div className="glass rounded-2xl p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {right}
      </div>
      {children}
    </div>
  );
}

// Collapsible section: click the header to slide the body open/closed.
function Collapsible({ title, right, defaultOpen = false, children }: { title: string; right?: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center justify-between gap-2 py-1 text-left">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <div className="flex items-center gap-2">
          {right}
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`text-ink-faint transition-transform duration-300 ${open ? "rotate-180" : ""}`}><path d="M6 9l6 6 6-6" /></svg>
        </div>
      </button>
      <div className="grid transition-all duration-300 ease-in-out" style={{ gridTemplateRows: open ? "1fr" : "0fr" }}>
        <div className="overflow-hidden">
          <div className="pt-3">{children}</div>
        </div>
      </div>
    </div>
  );
}

export default function SmsManager() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [queued, setQueued] = useState<QueuedLead[]>([]);
  const [active, setActive] = useState<ActiveLead[]>([]);
  const [pool, setPool] = useState<Pool>({ freshCount: 0, agedCount: 0, rejectedCount: 0 });
  const [board, setBoard] = useState<LeaderRow[]>([]);
  const [funnel, setFunnel] = useState<Funnel | null>(null);
  const [parkedA, setParkedA] = useState<{ total: number; items: ParkedItem[] }>({ total: 0, items: [] });
  const [parkedU, setParkedU] = useState<{ total: number; items: ParkedItem[] }>({ total: 0, items: [] });
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [availOpen, setAvailOpen] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [passKeep, setPassKeep] = useState<PassKeepRow[]>([]);
  const [pkPeriod, setPkPeriod] = useState<Period>("day");
  const [agentDisp, setAgentDisp] = useState<AgentDisp[]>([]);
  const [dispPeriod, setDispPeriod] = useState<Period>("day");
  const [yesLeads, setYesLeads] = useState<{ total: number; items: YesLead[] }>({ total: 0, items: [] });
  const [breaksToday, setBreaksToday] = useState<{ items: BreakRow[]; totals: BreakTotal[] }>({ items: [], totals: [] });
  const [daily, setDaily] = useState<DailyRow[]>([]);
  const [from, setFrom] = useState(weekAgoISO());
  const [to, setTo] = useState(todayISO());
  const [busy, setBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kickNotice, setKickNotice] = useState<string | null>(null);
  const [confirmKick, setConfirmKick] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);
  const [sendTo, setSendTo] = useState("");
  const [sendMsg, setSendMsg] = useState("");
  const [confirmDel, setConfirmDel] = useState<{ category: string; label: string } | null>(null);
  const [review, setReview] = useState<{ category: string; label: string } | null>(null);
  const [reviewData, setReviewData] = useState<{ total: number; items: ParkedItem[] }>({ total: 0, items: [] });
  const [reviewLoading, setReviewLoading] = useState(false);
  // Manage Leads → Parked/Restore tabs: review the already-fetched parked/yes leads
  // in a modal (frontend-only; reuses the existing per-item restore/delete/re-text).
  const [listView, setListView] = useState<{ title: string; kind: "parkedA" | "parkedU" | "yes" } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const rng = `from=${from}&to=${to}`;
      const [o, q, a, p, b, f, pa, pu, ac, pk, ad, yl, bt, ds] = await Promise.all([
        api<Overview>("/sms/manager/overview"),
        api<{ items: QueuedLead[] }>("/sms/manager/queued?limit=100"),
        api<{ items: ActiveLead[] }>("/sms/manager/active?limit=100"),
        api<Pool>("/sms/manager/pool-counts"),
        api<{ items: LeaderRow[] }>(`/sms/manager/leaderboard?${rng}`),
        api<Funnel>(`/sms/manager/funnel?${rng}`),
        api<{ total: number; items: ParkedItem[] }>("/sms/manager/parked?kind=ATTEMPTED"),
        api<{ total: number; items: ParkedItem[] }>("/sms/manager/parked?kind=UNQUALIFIED"),
        api<{ items: Activity[] }>(`/sms/manager/agent-activity?${rng}`),
        api<{ items: PassKeepRow[] }>(`/sms/manager/pass-keep?period=${pkPeriod}`),
        api<{ items: AgentDisp[] }>(`/sms/manager/agent-dispositions?period=${dispPeriod}`),
        api<{ total: number; items: YesLead[] }>("/sms/manager/yes-leads?limit=50"),
        api<{ items: BreakRow[]; totals: BreakTotal[] }>("/sms/manager/breaks-today"),
        api<{ items: DailyRow[] }>("/sms/manager/daily-summary"),
      ]);
      setOv(o); setQueued(q.items); setActive(a.items); setPool(p); setBoard(b.items);
      setFunnel(f); setParkedA(pa); setParkedU(pu);
      setActivity(ac.items);
      setPassKeep(pk.items); setAgentDisp(ad.items); setYesLeads(yl);
      setBreaksToday(bt); setDaily(ds.items);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, [from, to, pkPeriod, dispPeriod]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    const s = getSocket();
    const nudge = () => refresh();
    s.on("sms:queue_updated", nudge);
    s.on("sms:new_message", nudge);   // also refresh on a reply to an existing lead
    // Refresh the instant the tab is focused again (on top of the 6s poll + the
    // live sockets above), so a message that arrived while you were away shows
    // immediately on return.
    const onVisible = () => { if (!document.hidden) refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      clearInterval(id);
      s.off("sms:queue_updated", nudge); s.off("sms:new_message", nudge);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [refresh]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try { await fn(); await refresh(); } finally { setBusy(false); }
  }
  const post = (path: string, body?: unknown) =>
    api(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });

  const doKickAll = async () => {
    setConfirmKick(false);
    setBusy(true);
    try {
      const r = (await post("/sms/manager/kick-all")) as { kicked?: number };
      await refresh();
      setKickNotice(`Removed ${r?.kicked ?? 0} agents from queue`);
      setTimeout(() => setKickNotice(null), 3000);
    } finally { setBusy(false); }
  };
  const assignNext = () => act(() => post("/sms/manager/assign-next"));
  const rebroadcast = () => act(() => post("/sms/manager/rebroadcast"));
  const distributeAll = () => act(() => post("/sms/manager/distribute-all"));
  const reassign = (leadId: string, agentId: string) => act(() => post("/sms/manager/reassign", { lead_id: leadId, agent_user_id: agentId }));
  const markLead = (leadId: string, disposition: string) => act(() => post("/sms/manager/disposition", { lead_id: leadId, disposition }));
  const deleteLead = (leadId: string) => {
    setDeletingId(leadId);
    act(() => api(`/sms/manager/lead/${leadId}`, { method: "DELETE" })).finally(() => setDeletingId(null));
  };
  const restoreParked = (id: string) => act(() => post(`/sms/manager/parked/${id}/restore`));
  const loadReview = useCallback(async (category: string) => {
    setReviewLoading(true);
    try {
      const r = await api<{ total: number; items: ParkedItem[] }>(`/sms/manager/manage-leads?category=${category}&limit=500`);
      setReviewData(r);
    } finally { setReviewLoading(false); }
  }, []);
  const openReview = (category: string, label: string) => { setReview({ category, label }); setReviewData({ total: 0, items: [] }); loadReview(category); };
  const reviewDelete = (id: string) => {
    setDeletingId(id);
    act(() => api(`/sms/manager/lead/${id}`, { method: "DELETE" }))
      .then(() => review && loadReview(review.category))
      .finally(() => setDeletingId(null));
  };
  const reviewRestore = (id: string) => { act(() => post(`/sms/manager/parked/${id}/restore`)).then(() => review && loadReview(review.category)); };
  const reText = (phone: string) => { setSendTo(phone); setSendMsg(""); setSendOpen(true); };
  const bulkDelete = (category: string, label: string) => setConfirmDel({ category, label });
  const doBulkDelete = () => {
    if (!confirmDel) return;
    const category = confirmDel.category;
    setConfirmDel(null);
    act(() => post("/sms/manager/bulk-delete", { category }));
  };
  const sendSms = () =>
    act(async () => {
      await post("/sms/manager/send", { to_number: sendTo, message: sendMsg });
      setSendOpen(false); setSendTo(""); setSendMsg("");
    });

  if (error) return <div className="glass mx-auto max-w-2xl rounded-2xl p-6 text-danger">Failed to load: {error}</div>;
  if (!ov) return <div className="p-6 text-ink-muted">Loading…</div>;
  const agents = ov.agents;

  return (
    <div className="space-y-4">
      {kickNotice && (
        <div className="fixed bottom-6 right-6 z-50 rounded-lg bg-ink px-4 py-2 text-sm font-semibold text-white shadow-lg">{kickNotice}</div>
      )}

      {/* Agent Availability — all KPIs as the header + collapsible agent list */}
      <div className="glass rounded-2xl p-4">
        <button type="button" onClick={() => setAvailOpen((o) => !o)} aria-expanded={availOpen} className="flex w-full items-center justify-between gap-2 text-left">
          <h3 className="text-sm font-semibold text-ink">Agent Availability</h3>
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-faint">{active.length} in conversation</span>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`text-ink-faint transition-transform duration-300 ${availOpen ? "rotate-180" : ""}`}><path d="M6 9l6 6 6-6" /></svg>
          </div>
        </button>
        {/* All KPIs as the section header */}
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
          <div className="rounded-xl px-3 py-2" style={{ borderLeft: "5px solid #4f8268", background: "linear-gradient(rgba(79,130,104,0.16), rgba(79,130,104,0.16)), var(--panel-base, rgba(255,255,255,0.5))" }}>
            <div className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide text-success">✓ Yes (open)</div>
            <div className="text-xl font-semibold text-ink">{ov.counts.yes_open}</div>
            <div className="text-[10px] text-ink-muted">+{ov.counts.yes_today} today</div>
          </div>
          {[
            ["Queued", ov.counts.queued, "bg-blue-500"],
            ["On break", ov.counts.away, "bg-pending"],
            ["Online", ov.counts.talking + ov.counts.waiting + ov.counts.idle + ov.counts.away, "bg-success"],
            ["Talking", ov.counts.talking, ACTIVITY_DOT.talking],
            ["Waiting", ov.counts.waiting, ACTIVITY_DOT.waiting],
            ["Not talking", ov.counts.idle, ACTIVITY_DOT.idle],
            ["Wrapping", ov.counts.wrapping, "bg-purple-500"],
          ].map(([label, value, dot]) => (
            <div key={label as string} className="rounded-xl border border-hairline-soft bg-white/40 px-3 py-2">
              <div className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint"><span className={`h-2 w-2 rounded-full ${dot as string}`} />{label}</div>
              <div className="text-xl font-semibold text-ink">{value}</div>
            </div>
          ))}
        </div>
        {/* Agent list (collapsible body) */}
        <div className="grid transition-all duration-300 ease-in-out" style={{ gridTemplateRows: availOpen ? "1fr" : "0fr" }}>
          <div className="overflow-hidden">
        <div className="mt-3 max-h-[560px] space-y-1.5 overflow-y-auto">
          {agents.length === 0 && <div className="text-sm text-ink-muted">No agents.</div>}
          {agents.map((a) => {
            const act = a.wrapping ? "wrapping" : (a.activity || "offline");
            const convos = active.filter((l) => l.agent_name === a.name);
            const open = expandedAgent === a.user_id;
            return (
              <div key={a.user_id} className="overflow-hidden rounded-lg border border-hairline-soft">
                <button
                  type="button"
                  onClick={() => setExpandedAgent(open ? null : a.user_id)}
                  aria-expanded={open}
                  className="flex w-full items-center gap-2 px-2 py-2 text-left hover:bg-black/[0.025]"
                >
                  <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${ACTIVITY_DOT[act] || "bg-ink-faint"}`} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-ink">{a.name}</div>
                    <div className="text-[11px] text-ink-faint">
                      {act === "away" && a.break_reason
                        ? `${a.break_reason} · ${sinceMins(a.break_started_at)}`
                        : ACTIVITY_LABEL[act] || act}
                    </div>
                  </div>
                  {convos.length > 0 && <span className="shrink-0 rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-semibold text-accent">{convos.length} active</span>}
                  <span className="shrink-0 text-[11px] text-ink-faint">{a.total_leads_handled} handled</span>
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`shrink-0 text-ink-faint transition-transform duration-300 ${open ? "rotate-180" : ""}`}><path d="M6 9l6 6 6-6" /></svg>
                </button>
                <div className="grid transition-all duration-300 ease-in-out" style={{ gridTemplateRows: open ? "1fr" : "0fr" }}>
                  <div className="overflow-hidden">
                    <div className="space-y-2 px-2 pb-2 pt-1">
                      {convos.length === 0 ? (
                        <div className="py-2 text-center text-xs text-ink-muted">No active conversation right now.</div>
                      ) : convos.map((l) => (
                        <div key={l.id} className="rounded-xl border border-hairline-soft bg-white/60 p-3">
                          <div className="mb-1 text-sm font-semibold text-ink">{l.phone_number}</div>
                          <div className="mb-2 line-clamp-3 text-xs text-ink-muted">{l.last_message}</div>
                          <div className="flex items-center justify-between text-[11px] text-ink-faint"><span>{ago(l.accepted_at)}</span><span>{l.message_count} msgs</span></div>
                          <select className="mt-2 w-full rounded-lg border border-hairline bg-white px-2 py-1 text-xs" defaultValue="" disabled={busy} onChange={(e) => e.target.value && reassign(l.id, e.target.value)}>
                            <option value="" disabled>Reassign to…</option>
                            {agents.map((ag) => <option key={ag.user_id} value={ag.user_id}>{ag.name}</option>)}
                          </select>
                          <div className="mt-2 flex justify-end">
                            <button onClick={() => deleteLead(l.id)} disabled={busy} className="inline-flex items-center gap-1 text-xs font-semibold text-danger/60 hover:text-danger disabled:opacity-50" title="Permanently delete this conversation and its messages">{deletingId === l.id ? <Spinner /> : "Delete"}</button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
          </div>
        </div>
      </div>

      {/* Upload-Leads admin tools (Pause sending, Campaigns) — sits BELOW Agent Availability. Same /api/v1/ingestion endpoints, lockdown untouched. */}
      <LeadsTools />

      {/* Lead Pools */}
      <Panel title={`Lead Pools (${pool.freshCount + pool.agedCount} total, showing ${queued.length})`} right={
        <div className="flex gap-2">
          <button onClick={async () => { setRefreshing(true); try { await refresh(); } finally { setRefreshing(false); } }} disabled={refreshing} title="Fetch the latest leads & inbound messages now" className="rounded-lg border border-hairline px-3 py-1.5 text-xs font-semibold text-ink-muted hover:bg-black/5 hover:text-ink disabled:opacity-50">{refreshing ? "Refreshing…" : "↻ Refresh"}</button>
          <button onClick={rebroadcast} disabled={busy} title="Pull back leads offered but not accepted, then re-distribute" className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">Re-broadcast</button>
          <button onClick={assignNext} disabled={busy} className="rounded-lg bg-cyan-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">Assign Next</button>
          <button onClick={distributeAll} disabled={busy} className="rounded-lg bg-success px-3 py-1.5 text-xs font-bold text-white disabled:opacity-50">DISTRIBUTE ALL</button>
        </div>
      }>
        <div className="mb-3 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-success/15 px-2 py-1 font-semibold text-success">Fresh (&lt;15m): {pool.freshCount}</span>
          <span className="rounded-full bg-pending/15 px-2 py-1 font-semibold text-pending">Aged (15m+): {pool.agedCount}</span>
          <span className="rounded-full bg-danger/15 px-2 py-1 font-semibold text-danger">Rejected (2+ passes): {pool.rejectedCount}</span>
        </div>
        <div className="max-h-[360px] space-y-1 overflow-y-auto">
          {queued.length === 0 ? <div className="py-6 text-center text-sm text-ink-muted">No queued leads</div> : queued.map((l) => (
            <div key={l.id} className="flex items-center justify-between gap-3 rounded-lg border border-hairline-soft px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2"><span className="text-sm font-semibold text-ink">{l.phone_number}</span><span className="text-[10px] text-ink-faint">{ago(l.last_message_at ?? l.created_at)}</span></div>
                <div className="truncate text-xs text-ink-muted">{(l.last_message || "").slice(0, 60)}</div>
              </div>
              <select className="rounded-lg border border-hairline bg-white px-2 py-1 text-xs" defaultValue="" disabled={busy} onChange={(e) => e.target.value && reassign(l.id, e.target.value)}>
                <option value="" disabled>Assign to…</option>
                {agents.map((a) => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}
              </select>
              <select className="rounded-lg border border-hairline bg-white px-2 py-1 text-xs" value="" disabled={busy} title="Mark this lead without an agent" onChange={(e) => { if (e.target.value) { markLead(l.id, e.target.value); e.target.value = ""; } }}>
                <option value="" disabled>Mark…</option>
                <option value="WRONG_NUMBER">Wrong Number</option>
                <option value="UNQUALIFIED">Unqualified</option>
              </select>
              <button onClick={() => markLead(l.id, "UNQUALIFIED")} disabled={busy} title="Park as Unqualified (Do Not Call)" className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-danger text-[10px] font-bold text-white underline disabled:opacity-50">PU</button>
              <button onClick={() => deleteLead(l.id)} disabled={busy} className="flex w-5 items-center justify-center px-1 text-xs font-bold text-danger/60 hover:text-danger disabled:opacity-100" title="Delete lead">{deletingId === l.id ? <Spinner /> : "✕"}</button>
            </div>
          ))}
        </div>
      </Panel>

      {/* Performance + Breaks — two collapsible parts in one card (Performance, then Breaks) */}
      <div className="glass rounded-2xl p-4 space-y-2">
        <Collapsible title="Performance & Dispositions" defaultOpen>
        <div className="space-y-5">
          {/* Date range — filters Agent Lead Activity, Leaderboard & Funnel */}
          <div>
            <h4 className="mb-2 text-sm font-semibold text-ink">Date range</h4>
            <div className="flex items-center gap-3 text-sm">
              <input type="date" value={from} max={to || todayISO()} onChange={(e) => setFrom(e.target.value)} className="rounded-lg border border-hairline bg-white px-2 py-1 text-xs" />
              <span className="text-ink-faint">to</span>
              <input type="date" value={to} min={from} max={todayISO()} onChange={(e) => setTo(e.target.value)} className="rounded-lg border border-hairline bg-white px-2 py-1 text-xs" />
            </div>
          </div>

          {/* Agent Lead Activity */}
          <div className="border-t border-hairline-soft pt-4">
            <h4 className="mb-3 text-sm font-semibold text-ink">Agent Lead Activity</h4>
            {activity.length === 0 ? <div className="py-3 text-sm text-ink-muted">No activity in this range.</div> : (
              <table className="w-full text-sm">
                <thead><tr className="text-left text-xs uppercase tracking-wide text-ink-faint"><th className="pb-2">Agent</th><th className="pb-2">Accepted</th><th className="pb-2">Dispositioned</th></tr></thead>
                <tbody className="text-ink-soft">{activity.map((a) => (<tr key={a.agent_name} className="border-t border-hairline-soft"><td className="py-2 font-medium">{a.agent_name}</td><td className="py-2">{a.accepted}</td><td className="py-2">{a.dispositioned}</td></tr>))}</tbody>
              </table>
            )}
          </div>

          {/* Performance Leaderboard */}
          <div className="border-t border-hairline-soft pt-4">
            <h4 className="mb-3 text-sm font-semibold text-ink">Performance Leaderboard</h4>
            {board.length === 0 ? <div className="py-3 text-sm text-ink-muted">No closed leads in range.</div> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-xs uppercase tracking-wide text-ink-faint">
                    <th className="pb-2">Agent</th><th className="pb-2">Attempted</th><th className="pb-2">Replied</th><th className="pb-2">Reply rate</th><th className="pb-2">Sold</th><th className="pb-2">Conv rate</th><th className="pb-2">Avg response</th>
                  </tr></thead>
                  <tbody className="text-ink-soft">{board.map((r) => (
                    <tr key={r.agent_name} className="border-t border-hairline-soft">
                      <td className="py-2 font-medium">{r.agent_name}</td><td className="py-2">{r.attempted}</td><td className="py-2">{r.replied}</td><td className="py-2">{r.reply_rate_pct}%</td>
                      <td className="py-2 text-success">{r.sold}</td><td className="py-2">{r.conv_rate_pct}%</td><td className="py-2">{r.avg_response_ms ? `${Math.round(r.avg_response_ms / 1000)}s` : "—"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </div>

          {/* Passed vs Kept */}
          <div className="border-t border-hairline-soft pt-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h4 className="text-sm font-semibold text-ink">Passed vs Kept (per agent)</h4>
              <div className="flex gap-1">
                {PERIODS.map((p) => (
                  <button key={p} onClick={() => setPkPeriod(p)} className={`rounded-md px-2.5 py-1 text-xs font-semibold capitalize ${pkPeriod === p ? "bg-accent text-white" : "border border-hairline text-ink-muted hover:text-ink"}`}>{p}</button>
                ))}
              </div>
            </div>
            {passKeep.length === 0 ? <div className="py-3 text-sm text-ink-muted">No offers in this {pkPeriod}.</div> : (
              <table className="w-full text-sm">
                <thead><tr className="text-left text-xs uppercase tracking-wide text-ink-faint">
                  <th className="pb-2">Agent</th><th className="pb-2">Kept</th><th className="pb-2">Passed</th><th className="pb-2">Offered</th><th className="pb-2">Keep rate</th>
                </tr></thead>
                <tbody className="text-ink-soft">{passKeep.map((r) => (
                  <tr key={r.agent_name} className="border-t border-hairline-soft">
                    <td className="py-2 font-medium">{r.agent_name}</td>
                    <td className="py-2 text-success font-semibold">{r.kept}</td>
                    <td className="py-2 text-danger">{r.passed}</td>
                    <td className="py-2">{r.offered}</td>
                    <td className="py-2">{r.keep_rate_pct}%</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>

          {/* Agent Dispositions & Breaks */}
          <div className="border-t border-hairline-soft pt-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h4 className="text-sm font-semibold text-ink">Agent Dispositions & Breaks</h4>
              <div className="flex gap-1">
                {PERIODS.map((p) => (
                  <button key={p} onClick={() => setDispPeriod(p)} className={`rounded-md px-2.5 py-1 text-xs font-semibold capitalize ${dispPeriod === p ? "bg-blue-600 text-white" : "border border-hairline text-ink-muted hover:text-ink"}`}>{p}</button>
                ))}
              </div>
            </div>
            {agentDisp.length === 0 ? <div className="py-3 text-sm text-ink-muted">No activity in this {dispPeriod}.</div> : (
              <div className="space-y-2">
                {agentDisp.map((a) => (
                  <div key={a.agent_name} className="rounded-xl border border-hairline-soft bg-white/50 p-3">
                    <div className="mb-2 flex items-center justify-between">
                      <div className="text-sm font-semibold text-ink">{a.agent_name}</div>
                      <div className="text-[11px] text-ink-faint">{a.break_count} break{a.break_count === 1 ? "" : "s"} · {fmtDuration(a.break_seconds)} away</div>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(a.dispositions).length === 0 && Object.entries(a.breaks).length === 0 && (
                        <span className="text-xs text-ink-muted">No dispositions or breaks.</span>
                      )}
                      {Object.entries(a.dispositions).map(([d, n]) => (
                        <span key={d} className="rounded-full bg-black/5 px-2 py-0.5 text-[11px] font-medium text-ink-soft">{d.replace(/_/g, " ")}: <strong>{n}</strong></span>
                      ))}
                      {Object.entries(a.breaks).map(([r, n]) => (
                        <span key={r} className="rounded-full bg-pending/15 px-2 py-0.5 text-[11px] font-medium text-pending">{r}: <strong>{n}</strong></span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        </Collapsible>

        <div className="border-t border-hairline-soft" />

        <Collapsible title="Breaks & Daily Summary">
        <div className="space-y-5">
          <div>
            <h4 className="mb-3 text-sm font-semibold text-ink">Breaks Today</h4>
        {breaksToday.items.length === 0 ? <div className="py-3 text-sm text-ink-muted">No breaks today.</div> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs uppercase tracking-wide text-ink-faint">
                <th className="pb-2">Agent</th><th className="pb-2">Reason</th><th className="pb-2">Start</th><th className="pb-2">End</th><th className="pb-2">Duration</th>
              </tr></thead>
              <tbody className="text-ink-soft">
                {breaksToday.items.map((b, i) => (
                  <tr key={i} className="border-t border-hairline-soft">
                    <td className="py-2 font-medium">{b.agent_name}</td>
                    <td className="py-2">{b.reason}</td>
                    <td className="py-2">{fmtClock(b.started_at)}</td>
                    <td className="py-2">{b.ongoing ? <span className="font-semibold text-pending">ongoing</span> : fmtClock(b.ended_at)}</td>
                    <td className="py-2">{fmtHMS(b.duration_seconds)}</td>
                  </tr>
                ))}
                {breaksToday.totals.map((t) => (
                  <tr key={`tot-${t.agent_name}`} className="border-t border-hairline font-semibold text-ink">
                    <td className="py-2" colSpan={4}>Total — {t.agent_name}</td>
                    <td className="py-2">{fmtHMS(t.total_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
          </div>

          <div className="border-t border-hairline-soft pt-4">
            <h4 className="mb-3 text-sm font-semibold text-ink">Daily Summary</h4>
        {daily.length === 0 ? <div className="py-3 text-sm text-ink-muted">No activity today.</div> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs uppercase tracking-wide text-ink-faint">
                <th className="pb-2">Agent</th><th className="pb-2">Shift</th><th className="pb-2">Break</th><th className="pb-2">Billable</th><th className="pb-2">Applications</th><th className="pb-2">Appts</th><th className="pb-2">Avg Resp</th><th className="pb-2">Conv</th>
              </tr></thead>
              <tbody className="text-ink-soft">{daily.map((d) => (
                <tr key={d.agent_name} className="border-t border-hairline-soft">
                  <td className="py-2 font-medium">{d.agent_name}</td>
                  <td className="py-2">{fmtHMS(d.shift_seconds)}</td>
                  <td className="py-2">{fmtHMS(d.break_seconds)}</td>
                  <td className="py-2">{fmtHMS(d.billable_seconds)}</td>
                  <td className="py-2">{d.applications}</td>
                  <td className="py-2">{d.appts}</td>
                  <td className="py-2">{d.avg_response_ms ? `${Math.round(d.avg_response_ms / 1000)}s` : "0s"}</td>
                  <td className="py-2">{d.conv_pct}%</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
          </div>
        </div>
        </Collapsible>
      </div>

      {/* Manage Leads — collapsible card: Funnel first, then the category tabs */}
      <div className="glass rounded-2xl p-4">
        <Collapsible title="Manage Leads" defaultOpen>
        <div className="space-y-5">
          {funnel && (
            <div>
              <h4 className="mb-3 text-sm font-semibold text-ink">Funnel · {funnel.from} → {funnel.to}</h4>
              <div className="grid grid-cols-3 gap-3 text-center">
                <div className="rounded-xl border border-hairline-soft p-4"><div className="text-xs uppercase text-ink-faint">Attempted</div><div className="mt-1 text-3xl font-semibold text-ink">{funnel.attempted}</div></div>
                <div className="rounded-xl border border-hairline-soft p-4"><div className="text-xs uppercase text-ink-faint">Replied</div><div className="mt-1 text-3xl font-semibold text-ink">{funnel.replied}</div><div className="text-xs text-ink-muted">{funnel.replied_pct}% of attempted</div></div>
                <div className="rounded-xl border border-hairline-soft p-4"><div className="text-xs uppercase text-ink-faint">Sold</div><div className="mt-1 text-3xl font-semibold text-success">{funnel.sold}</div><div className="text-xs text-ink-muted">{funnel.sold_pct}% of attempted</div></div>
              </div>
            </div>
          )}
          <div className={funnel ? "border-t border-hairline-soft pt-4" : ""}>
            <div className="grid gap-3 md:grid-cols-3">
              {[
                { category: "wrong_number", label: "Wrong Numbers" },
                { category: "rejected_blocked", label: "Rejected/Blocked" },
                { category: "couldnt_sell", label: "Couldn't Sell" },
              ].map((c) => (
                <div key={c.category} className="rounded-xl border border-hairline-soft bg-white/50 p-3">
                  <div className="mb-2 text-sm font-semibold text-ink">{c.label}</div>
                  <div className="flex gap-2">
                    <button onClick={() => openReview(c.category, c.label)} disabled={busy} className="flex-1 rounded-lg bg-black/5 px-3 py-2 text-xs font-semibold text-ink hover:bg-black/10 disabled:opacity-50">Review</button>
                    <button onClick={() => bulkDelete(c.category, c.label)} disabled={busy} className="flex-1 rounded-lg bg-danger/10 px-3 py-2 text-xs font-semibold text-danger hover:bg-danger/15 disabled:opacity-50">Delete All</button>
                  </div>
                </div>
              ))}
              {/* Parked / Restore tabs — same card style; open the already-fetched
                  parked/yes leads in a review modal (UI only; backend untouched). */}
              {[
                { kind: "parkedA" as const, label: "Parked — Attempted", title: "Parked — Attempted (No Answer)", total: parkedA.total },
                { kind: "parkedU" as const, label: "Parked — Unqualified", title: "Parked — Unqualified (Do Not Call)", total: parkedU.total },
                { kind: "yes" as const, label: "Restore", title: 'Restore a "Yes" — re-text closed leads', total: yesLeads.total },
              ].map((c) => (
                <div key={c.kind} className="rounded-xl border border-hairline-soft bg-white/50 p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-ink">{c.label}</span>
                    <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-semibold text-ink-muted">{c.total}</span>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => setListView({ title: c.title, kind: c.kind })} disabled={busy} className="flex-1 rounded-lg bg-black/5 px-3 py-2 text-xs font-semibold text-ink hover:bg-black/10 disabled:opacity-50">Review</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
        </Collapsible>
      </div>

      {/* Send SMS modal */}
      {sendOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => setSendOpen(false)}>
          <div className="glass w-full max-w-md rounded-2xl p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-3 text-lg font-semibold text-ink">Send SMS</h3>
            <input value={sendTo} onChange={(e) => setSendTo(e.target.value)} placeholder="+1XXXXXXXXXX" className="mb-2 w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm" />
            <textarea value={sendMsg} onChange={(e) => setSendMsg(e.target.value)} placeholder="Message…" rows={3} className="mb-3 w-full rounded-lg border border-hairline bg-white px-3 py-2 text-sm" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setSendOpen(false)} className="rounded-lg border border-hairline px-3 py-2 text-sm text-ink-muted">Cancel</button>
              <button onClick={sendSms} disabled={busy || !sendTo.trim() || !sendMsg.trim()} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">Send</button>
            </div>
            <p className="mt-2 text-[11px] text-ink-faint">Records the message locally; real Sinch send is wired when credentials are live.</p>
          </div>
        </div>
      )}

      {/* Review (Manage Leads) modal */}
      {review && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => setReview(null)}>
          <div className="glass w-full max-w-lg rounded-2xl p-5" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-lg font-semibold text-ink">{review.label} · {reviewData.total}</h3>
              <button onClick={() => setReview(null)} className="text-ink-faint hover:text-ink">✕</button>
            </div>
            <div className="max-h-80 space-y-1 overflow-y-auto">
              {reviewLoading ? (
                <div className="py-8 text-center text-sm text-ink-muted">Loading…</div>
              ) : reviewData.items.length === 0 ? (
                <div className="py-8 text-center text-sm text-ink-muted">No leads in this category.</div>
              ) : reviewData.items.map((m) => (
                <div key={m.id} className="flex items-center justify-between gap-2 rounded-lg border border-hairline-soft px-3 py-2 text-xs">
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-ink">{m.phone_number}</div>
                    <div className="truncate text-ink-muted">{m.last_message}</div>
                  </div>
                  <span className="text-ink-faint">{m.agent_name}</span>
                  <button onClick={() => reviewRestore(m.id)} disabled={busy} className="text-accent hover:underline">Restore</button>
                  <button onClick={() => reviewDelete(m.id)} disabled={busy} className="inline-flex items-center gap-1 text-danger/60 hover:text-danger">{deletingId === m.id ? <Spinner /> : "Delete"}</button>
                </div>
              ))}
            </div>
            <div className="mt-4 flex justify-between gap-2">
              <button onClick={() => setReview(null)} className="rounded-lg border border-hairline px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink">Close</button>
              <button onClick={() => { const r = review; setReview(null); bulkDelete(r.category, r.label); }} disabled={busy || reviewData.total === 0} className="rounded-lg bg-danger px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50">Delete All</button>
            </div>
          </div>
        </div>
      )}

      {/* Parked / Restore review modal (Manage Leads tabs) — reads live state so it
          stays in sync after a restore/delete/re-text; backend is untouched. */}
      {listView && (() => {
        const src = listView.kind === "parkedA" ? parkedA : listView.kind === "parkedU" ? parkedU : yesLeads;
        const items = src.items as Array<ParkedItem & { customer_name?: string; disposition?: string }>;
        const isYes = listView.kind === "yes";
        return (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => setListView(null)}>
            <div className="glass w-full max-w-lg rounded-2xl p-5" onClick={(e) => e.stopPropagation()}>
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-lg font-semibold text-ink">{listView.title} · {src.total}</h3>
                <button onClick={() => setListView(null)} className="text-ink-faint hover:text-ink">✕</button>
              </div>
              <div className="max-h-80 space-y-1 overflow-y-auto">
                {items.length === 0 ? (
                  <div className="py-8 text-center text-sm text-ink-muted">{isYes ? 'No closed "yes" leads yet.' : "None."}</div>
                ) : items.map((m) => (
                  <div key={m.id} className="flex items-center justify-between gap-2 rounded-lg border border-hairline-soft px-3 py-2 text-xs">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-ink">{m.customer_name || m.phone_number}</span>
                        {m.disposition && <span className="rounded bg-black/5 px-1.5 py-0.5 text-[10px] text-ink-muted">{m.disposition.replace(/_/g, " ")}</span>}
                      </div>
                      <div className="truncate text-ink-muted">{isYes ? `${m.phone_number} · ${(m.last_message || "").slice(0, 50)}` : m.last_message}</div>
                    </div>
                    {isYes && <button onClick={() => reText(m.phone_number)} disabled={busy} className="font-semibold text-accent hover:underline disabled:opacity-50">Re-text</button>}
                    <button onClick={() => restoreParked(m.id)} disabled={busy} title="Put back in the queue" className="font-semibold text-success hover:underline disabled:opacity-50">Restore</button>
                    {!isYes && <button onClick={() => deleteLead(m.id)} disabled={busy} className="inline-flex items-center gap-1 text-danger/60 hover:text-danger disabled:opacity-50">{deletingId === m.id ? <Spinner /> : "Delete"}</button>}
                  </div>
                ))}
              </div>
              <div className="mt-4 flex justify-end">
                <button onClick={() => setListView(null)} className="rounded-lg border border-hairline px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink">Close</button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Kick-all confirmation modal */}
      {confirmKick && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => setConfirmKick(false)}>
          <div className="glass w-full max-w-sm rounded-2xl p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-1 text-lg font-semibold text-ink">Remove ALL agents from the queue?</h3>
            <p className="mb-4 text-sm text-ink-muted">This will log everyone out. Any leads they were holding go back to the queue.</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmKick(false)} className="rounded-lg border border-hairline px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink">Cancel</button>
              <button onClick={doKickAll} disabled={busy} className="rounded-lg bg-danger px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50">Kick All</button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk-delete confirmation modal */}
      {confirmDel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => setConfirmDel(null)}>
          <div className="glass w-full max-w-sm rounded-2xl p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-1 text-lg font-semibold text-ink">Delete all {confirmDel.label}?</h3>
            <p className="mb-4 text-sm text-ink-muted">This permanently removes those leads and their messages. This cannot be undone.</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmDel(null)} className="rounded-lg border border-hairline px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink">Cancel</button>
              <button onClick={doBulkDelete} disabled={busy} className="rounded-lg bg-danger px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50">Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

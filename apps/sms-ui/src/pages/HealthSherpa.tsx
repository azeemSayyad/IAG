/* HealthSherpa 2027 authorizations.
 *
 * Upload a sheet of clients, press Start on one, and the IAG Chrome extension
 * runs the HealthSherpa steps in the agent's own logged-in tab:
 *   Search Marketplace -> fill name/DOB/state -> Plan Year 2027 -> Add to clients
 *   -> Include consent -> Text link -> wait for the client -> Finish.
 * The client still taps the link and authorizes on THEIR phone — nothing here
 * (or in the extension) ever does that part.
 *
 * Statuses come from the server (the extension reports each step with a
 * one-job token); the page polls fast while a job is running.
 *
 * The "Text campaigns" tab (components/hs/HsCampaigns.tsx) is the HealthSherpa
 * TEXT CAMPAIGN flow — separate from the Auto Dialer's. Its clients are queued
 * by their own text replies and run by AUTO MODE (components/hs/useAutoMode.ts),
 * which lives here so it keeps running whichever tab is open.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiUpload } from "../lib/api";
import { Drawer, Field, drawerCtl } from "../components/Drawer";
import {
  focusHealthSherpa, sendJob, stopJob, useExtension, useExtensionUpdates,
  type ExtUpdate, type HsJob,
} from "../lib/hsBridge";
import { Banner, Empty, Menu, MenuItem, ago, errText, fmtDob, fmtPhone } from "../components/hs/ui";
import HsCampaigns, { type HsOverview } from "../components/hs/HsCampaigns";
import { autoLabel, useAutoMode } from "../components/hs/useAutoMode";
import { LoginAlert, useHsStatus } from "../components/hs/HsAutomation";

type Row = {
  id: string;
  batch_id: string | null;
  row_number: number | null;
  first_name: string;
  last_name: string;
  dob: string | null;
  state: string | null;
  phone: string | null;
  email: string | null;
  assigned_to: string | null;
  assigned_name: string | null;
  started_by_name: string | null;
  status: string;
  status_detail: string | null;
  step: string | null;
  hs_app_id: string | null;
  link_sends: number;
  runs: number;
  started_at: string | null;
  link_sent_at: string | null;
  authorized_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
  auto?: boolean;
  events?: { at: string; step: string | null; status: string | null; detail: string }[];
};
type Usage = { used: number; limit: number; left: number };
type Batch = { id: string; name: string; created_at: string; total_rows: number; invalid_rows: number };
type ListResp = {
  rows: Row[]; total: number; counts: Record<string, number>; usage: Usage;
  batches: Batch[]; max_resends: number; is_manager: boolean;
};
type Agent = { id: string; name: string; role: string };

const STATUS: Record<string, { label: string; cls: string; hint: string }> = {
  PENDING: { label: "Ready", cls: "bg-accent/10 text-accent", hint: "Ready to start." },
  IN_PROGRESS: { label: "Running", cls: "bg-accent text-white", hint: "The extension is working in HealthSherpa." },
  LINK_SENT: { label: "Link sent", cls: "bg-pending/15 text-pending", hint: "Waiting for the client to tap the link and authorize." },
  AUTHORIZED: { label: "Authorized", cls: "bg-success/15 text-success", hint: "The client authorized you for 2027." },
  NOT_FOUND: { label: "Not found", cls: "bg-black/5 text-ink-muted", hint: "No 2027 application found." },
  NEEDS_REVIEW: { label: "Needs review", cls: "bg-pending/15 text-pending", hint: "Stopped on purpose — check it yourself in HealthSherpa." },
  EXPIRED: { label: "Expired", cls: "bg-danger/10 text-danger", hint: "The client didn't authorize in time." },
  FAILED: { label: "Failed", cls: "bg-danger/10 text-danger", hint: "Something unexpected happened." },
  CANCELLED: { label: "Stopped", cls: "bg-black/5 text-ink-muted", hint: "Stopped by you." },
  INVALID: { label: "Fix data", cls: "bg-danger/10 text-danger", hint: "This row can't be searched — Edit it." },
};
const FILTERS: [string, string][] = [
  ["ALL", "All"], ["PENDING", "Ready"], ["IN_PROGRESS", "Running"], ["LINK_SENT", "Link sent"],
  ["AUTHORIZED", "Authorized"], ["NEEDS_REVIEW", "Needs review"], ["NOT_FOUND", "Not found"],
  ["EXPIRED", "Expired"], ["FAILED", "Failed"], ["INVALID", "Fix data"], ["CANCELLED", "Stopped"],
];
const ACTIVE = new Set(["IN_PROGRESS", "LINK_SENT"]);
const STARTABLE = new Set(["PENDING", "NOT_FOUND", "NEEDS_REVIEW", "EXPIRED", "FAILED", "CANCELLED"]);
const LINK_MINUTES = 15;

const STEP_LABEL: Record<string, string> = {
  start: "Starting", open: "Opening HealthSherpa", login: "Waiting for you to log in",
  search_page: "Opening Search Marketplace", fill: "Filling client details", search: "Searching the Marketplace",
  results: "Reading results", add: "Adding the 2027 application", popup: "Authorization window",
  consent: "Ticking consent", link: "Link sent — waiting for the client", refresh: "Checking status",
  resend: "Resending the link", finish: "Finishing", done: "Done",
};

const btn = "rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white hover:bg-accent-hover disabled:opacity-50";
const btnGhost = "rounded-lg border border-hairline px-3 py-1.5 text-xs font-semibold text-ink-muted hover:bg-black/5 hover:text-ink disabled:opacity-50";
const inputCls = "rounded-lg border border-hairline bg-white px-2 py-1.5 text-xs text-ink";

const TEMPLATE = "First Name,Last Name,Date of Birth,State,Phone,Email\nJane,Doe,01/31/1985,FL,(555) 123-4567,jane@example.com\n";

export default function HealthSherpa() {
  const ext = useExtension();
  const [data, setData] = useState<ListResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [filter, setFilter] = useState("ALL");
  const [q, setQ] = useState("");
  const [agentF, setAgentF] = useState("");
  const [batchF, setBatchF] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [live, setLive] = useState<Record<string, ExtUpdate>>({});
  const [confirmRow, setConfirmRow] = useState<Row | null>(null);
  const [consent, setConsent] = useState(false);
  const [editRow, setEditRow] = useState<Row | null>(null);
  const [historyRow, setHistoryRow] = useState<Row | null>(null);
  const [showInstall, setShowInstall] = useState(false);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [, tick] = useState(0);
  const [tab, setTab] = useState<"clients" | "campaigns">(() => {
    // Text campaigns is the main flow (texts first, starts from a YES); the
    // manual list is opened on purpose. ("hsTab2": older browsers remembered
    // the manual tab under "hsTab".)
    try { return localStorage.getItem("hsTab2") === "clients" ? "clients" : "campaigns"; } catch { return "campaigns"; }
  });
  const [hsOverview, setHsOverview] = useState<HsOverview | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams();
    if (filter !== "ALL") p.set("status", filter);
    if (q.trim()) p.set("q", q.trim());
    if (agentF) p.set("agent", agentF);
    if (batchF) p.set("batch", batchF);
    p.set("limit", "300");
    return api<ListResp>(`/hs-auth/requests?${p}`)
      .then((r) => { setData(r); setErr(null); })
      .catch((e) => setErr(errText(e)));
  }, [filter, q, agentF, batchF]);

  useEffect(() => { const t = window.setTimeout(load, q ? 300 : 0); return () => window.clearTimeout(t); }, [load, q]);

  const running = useMemo(() => (data?.rows || []).filter((r) => ACTIVE.has(r.status)), [data]);
  // Poll fast while something runs, slowly otherwise (another agent / tab).
  useEffect(() => {
    const t = window.setInterval(load, running.length ? 3000 : 15000);
    return () => window.clearInterval(t);
  }, [load, running.length]);
  // Re-render each second for the 15-minute countdown.
  useEffect(() => {
    if (!running.some((r) => r.status === "LINK_SENT")) return;
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [running]);

  useEffect(() => {
    if (data?.is_manager) api<Agent[]>("/hs-auth/agents").then(setAgents).catch(() => setAgents([]));
  }, [data?.is_manager]);

  // HealthSherpa text campaigns: managers + the campaign's HealthSherpa agent
  // (403 for everyone else — then the tab simply isn't there).
  const loadOverview = useCallback(() => {
    api<HsOverview>("/hs-auth/campaigns").then(setHsOverview).catch(() => setHsOverview(null));
  }, []);
  const auto = useAutoMode(ext, !!hsOverview?.is_hs_agent, load);
  useEffect(() => {
    loadOverview();
    const t = window.setInterval(loadOverview, tab === "campaigns" || auto.active ? 5000 : 30000);
    return () => window.clearInterval(t);
  }, [loadOverview, tab, auto.active]);
  // The automation's full picture (campaign tab panel + the login popup on any tab).
  const hsStatus = useHsStatus(!!hsOverview);
  const pickTab = (t: "clients" | "campaigns") => {
    setTab(t);
    try { localStorage.setItem("hsTab2", t); } catch { /* private window */ }
  };
  const showCampaigns = !!hsOverview && tab === "campaigns";

  const onUpdate = useCallback((u: ExtUpdate) => {
    setLive((cur) => ({ ...cur, [u.id]: u }));
    if (u.status) load();
  }, [load]);
  useExtensionUpdates(onUpdate);

  const doStart = async (row: Row) => {
    setBusyId(row.id); setErr(null); setNote(null);
    try {
      const r = await api<{ job: HsJob; usage: Usage }>(`/hs-auth/requests/${row.id}/start`, { method: "POST" });
      try {
        await sendJob(r.job);
        setNote(`Started ${row.first_name} ${row.last_name} — watch the HealthSherpa tab.`);
      } catch (e) {
        // The extension never got it: give the slot's row back so it can be retried.
        await api(`/hs-auth/requests/${row.id}/cancel`, { method: "POST" }).catch(() => {});
        throw e;
      }
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusyId(null);
      load();
    }
  };

  const doStop = async (row: Row) => {
    setBusyId(row.id);
    stopJob(row.id);
    try { await api(`/hs-auth/requests/${row.id}/cancel`, { method: "POST" }); }
    catch (e) { setErr(errText(e)); }
    finally { setBusyId(null); load(); }
  };

  const doReset = async (row: Row) => {
    setBusyId(row.id);
    try { await api(`/hs-auth/requests/${row.id}/reset`, { method: "POST" }); }
    catch (e) { setErr(errText(e)); }
    finally { setBusyId(null); load(); }
  };

  const doRemove = async (row: Row) => {
    if (!window.confirm(`Remove ${row.first_name} ${row.last_name} from this list?`)) return;
    setBusyId(row.id);
    try { await api(`/hs-auth/requests/${row.id}`, { method: "DELETE" }); }
    catch (e) { setErr(errText(e)); }
    finally { setBusyId(null); load(); }
  };

  const openHistory = (row: Row) => {
    api<Row>(`/hs-auth/requests/${row.id}`).then(setHistoryRow).catch((e) => setErr(errText(e)));
  };

  const usage = data?.usage;
  const limitHit = !!usage && usage.left <= 0;
  const myRunning = running[0] || null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-extrabold text-ink">HealthSherpa Authorizations</h1>
          <p className="text-xs text-ink-muted">
            Upload clients, press Start, and the Chrome extension does the 2027 authorization steps in HealthSherpa for you.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {usage && (
            <span className={`rounded-full px-3 py-1 text-xs font-bold tabular-nums ${limitHit ? "bg-danger/10 text-danger" : "bg-black/5 text-ink-soft"}`}
                  title="Starts today (resets at midnight Eastern)">
              Today: {usage.used} / {usage.limit}
            </span>
          )}
          {hsOverview?.is_hs_agent && (
            <button onClick={() => auto.toggle(!auto.on)} disabled={!ext} data-testid="hs-auto-toggle"
                    title={!ext ? "Install the Chrome extension first" : "Run answered text-campaign clients in HealthSherpa automatically. Keep this tab open."}
                    className={`flex items-center gap-2 rounded-full px-3 py-1 text-xs font-bold disabled:opacity-50 ${auto.active ? "bg-accent text-white" : "bg-black/5 text-ink-soft hover:text-ink"}`}>
              <span className={`inline-block h-3.5 w-6 rounded-full p-0.5 ${auto.active ? "bg-white/40" : "bg-black/15"}`}>
                <span className={`block h-2.5 w-2.5 rounded-full bg-white transition ${auto.active ? "translate-x-2.5" : ""}`} />
              </span>
              Auto mode {auto.active ? "on" : "off"}
              {!!hsOverview.my_queue && !auto.active && <span className="rounded-full bg-accent px-1.5 text-white">{hsOverview.my_queue}</span>}
            </button>
          )}
          {ext ? (
            <span className="rounded-full bg-success/15 px-3 py-1 text-xs font-bold text-success">
              ● Extension connected <span className="font-normal opacity-70">v{ext.version}</span>
            </span>
          ) : (
            <button onClick={() => setShowInstall(true)}
                    className="rounded-full bg-danger/10 px-3 py-1 text-xs font-bold text-danger hover:bg-danger/15">
              ● Extension not installed — how to install
            </button>
          )}
        </div>
      </div>

      {hsOverview && (
        <div className="flex gap-1 border-b border-hairline" role="tablist">
          {([["campaigns", "Text campaigns"], ["clients", "Manual start (no texting)"]] as const).map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => pickTab(k)}
                    className={`-mb-px border-b-2 px-3 py-2 text-xs font-bold ${tab === k ? "border-accent text-accent" : "border-transparent text-ink-muted hover:text-ink"}`}>
              {label}
            </button>
          ))}
        </div>
      )}

      {auto.active && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 text-xs" data-testid="hs-auto-status">
          <span><b className="text-accent">Auto mode</b> · {autoLabel(auto.info, usage?.left)}. Keep this tab open and HealthSherpa logged in.</span>
          <button className={btnGhost} onClick={() => auto.toggle(false)}>Turn off</button>
        </div>
      )}
      <LoginAlert st={hsStatus.st} meIsAgent={!!hsOverview?.is_hs_agent} />
      {auto.error && <Banner tone="danger" onClose={auto.clearError}>{auto.error}</Banner>}
      {err && <Banner tone="danger" onClose={() => setErr(null)}>{err}</Banner>}
      {note && <Banner tone="success" onClose={() => setNote(null)}>{note}</Banner>}

      {myRunning && <RunningCard row={myRunning} live={live[myRunning.id]} busy={busyId === myRunning.id}
                                 maxResends={data?.max_resends ?? 1} onStop={() => doStop(myRunning)} />}

      {showCampaigns && (
        <HsCampaigns overview={hsOverview!} reloadOverview={loadOverview} agents={agents}
                     status={hsStatus.st} statusAt={hsStatus.at}
                     onEdit={(c) => setEditRow(c as unknown as Row)} onError={(m) => m && setErr(m)}
                     onNote={(m) => { setNote(m); setErr(null); }}
                     onRunning={(c) => {
                       // One button: the campaign's HealthSherpa agent pressing Run on
                       // their own computer also starts auto mode here.
                       if (c.hs_agent_id === hsOverview!.me && ext) auto.toggle(true);
                       else if (c.hs_agent_id === hsOverview!.me) setErr("Texting started. Install the Chrome extension on this computer to run HealthSherpa automatically.");
                       else setNote(`Texting started. On ${c.hs_agent_name || "the HealthSherpa agent"}'s computer, turn on Auto mode on this page.`);
                     }} />
      )}

      {!showCampaigns && <>
      {hsOverview && (
        <div className="rounded-lg border border-pending/30 bg-pending/10 px-3 py-2 text-xs text-ink-soft">
          <b className="text-pending">This list never texts anyone.</b> You press Start for each client yourself.
          To text clients and start from their YES reply, use{" "}
          <button className="font-bold text-accent underline" onClick={() => pickTab("campaigns")}>Text campaigns</button>.
        </div>
      )}
      <UploadCard isManager={!!data?.is_manager} agents={agents}
                  onDone={(msg) => { setNote(msg); setErr(null); load(); }} onError={setErr} />

      <div className="glass rounded-2xl p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {FILTERS.map(([key, label]) => {
            const n = key === "ALL"
              ? Object.values(data?.counts || {}).reduce((a, b) => a + b, 0)
              : data?.counts?.[key] || 0;
            if (key !== "ALL" && key !== "PENDING" && !n && filter !== key) return null;
            return (
              <button key={key} onClick={() => setFilter(key)}
                      className={`rounded-full px-3 py-1 text-xs font-semibold ${filter === key ? "bg-accent text-white" : "bg-black/5 text-ink-muted hover:text-ink"}`}>
                {label} <span className="tabular-nums opacity-70">{n}</span>
              </button>
            );
          })}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input className={`${inputCls} w-56`} placeholder="Search name, phone, app ID…" value={q}
                 onChange={(e) => setQ(e.target.value)} />
          {data?.is_manager && (
            <select className={inputCls} value={agentF} onChange={(e) => setAgentF(e.target.value)}>
              <option value="">All agents</option>
              <option value="open">Not assigned</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          )}
          {!!data?.batches?.length && (
            <select className={`${inputCls} max-w-56`} value={batchF} onChange={(e) => setBatchF(e.target.value)}>
              <option value="">All uploads</option>
              {data.batches.map((b) => (
                <option key={b.id} value={b.id}>{b.name} · {new Date(b.created_at).toLocaleDateString()}</option>
              ))}
            </select>
          )}
        </div>

        {!data ? (
          <Empty>Loading…</Empty>
        ) : data.rows.length === 0 ? (
          <Empty>
            {filter === "ALL" && !q ? "No clients yet — upload a sheet above to begin." : "Nothing matches these filters."}
          </Empty>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead>
                <tr className="border-b border-hairline text-[0.68rem] uppercase tracking-wide text-ink-faint">
                  <th className="py-2 pr-3 font-bold">Client</th>
                  <th className="py-2 pr-3 font-bold">DOB · State</th>
                  <th className="py-2 pr-3 font-bold">Phone</th>
                  {data.is_manager && <th className="py-2 pr-3 font-bold">Agent</th>}
                  <th className="py-2 pr-3 font-bold">Status</th>
                  <th className="py-2 pr-3 text-right font-bold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline-soft">
                {data.rows.map((r) => {
                  const st = STATUS[r.status] || STATUS.FAILED;
                  const busy = busyId === r.id;
                  const canStart = STARTABLE.has(r.status) && !myRunning;
                  return (
                    <tr key={r.id} className="align-top">
                      <td className="py-2.5 pr-3">
                        <div className="font-bold text-ink">{r.first_name} {r.last_name}</div>
                        {r.hs_app_id && <div className="text-[0.68rem] text-ink-faint">App ID {r.hs_app_id}</div>}
                      </td>
                      <td className="py-2.5 pr-3 tabular-nums text-ink-soft">{fmtDob(r.dob)} · {r.state || "—"}</td>
                      <td className="py-2.5 pr-3 tabular-nums text-ink-soft">{fmtPhone(r.phone) || <span className="text-ink-faint">—</span>}</td>
                      {data.is_manager && (
                        <td className="py-2.5 pr-3 text-ink-soft">{r.assigned_name || <span className="text-ink-faint">Anyone</span>}</td>
                      )}
                      <td className="max-w-[280px] py-2.5 pr-3">
                        <span className={`inline-block rounded-full px-2 py-0.5 text-[0.68rem] font-bold ${st.cls}`} title={st.hint}>
                          {st.label}
                        </span>
                        {r.status_detail && r.status !== "AUTHORIZED" && (
                          <div className="mt-1 text-[0.7rem] leading-snug text-ink-muted">{r.status_detail}</div>
                        )}
                        {r.status === "AUTHORIZED" && r.authorized_at && (
                          <div className="mt-1 text-[0.7rem] text-ink-faint">{new Date(r.authorized_at).toLocaleString()}</div>
                        )}
                        {r.status !== "AUTHORIZED" && r.updated_at && !ACTIVE.has(r.status) && r.status !== "PENDING" && (
                          <div className="text-[0.66rem] text-ink-faint">{ago(r.updated_at)}</div>
                        )}
                      </td>
                      <td className="py-2.5 text-right">
                        <div className="inline-flex flex-wrap justify-end gap-1">
                          {ACTIVE.has(r.status) && (
                            <button className={btnGhost} disabled={busy} onClick={() => doStop(r)}>Stop</button>
                          )}
                          {STARTABLE.has(r.status) && (
                            <button className={btn}
                                    disabled={busy || !canStart || !ext || limitHit}
                                    title={!ext ? "Install the Chrome extension first" : myRunning ? "Finish the running client first" : limitHit ? "Daily limit reached" : ""}
                                    onClick={() => { setConsent(false); setConfirmRow(r); }}>
                              {busy ? "…" : r.status === "PENDING" ? "Start" : "Retry"}
                            </button>
                          )}
                          {r.status !== "AUTHORIZED" && !ACTIVE.has(r.status) && (
                            <button className={btnGhost} disabled={busy} onClick={() => setEditRow(r)}>Edit</button>
                          )}
                          <Menu>
                            <MenuItem onClick={() => openHistory(r)}>History</MenuItem>
                            {(r.status === "AUTHORIZED" || r.status === "EXPIRED" || r.status === "NOT_FOUND" || r.status === "NEEDS_REVIEW" || r.status === "FAILED" || r.status === "CANCELLED") && (
                              <MenuItem onClick={() => doReset(r)}>Move back to Ready</MenuItem>
                            )}
                            {!ACTIVE.has(r.status) && <MenuItem danger onClick={() => doRemove(r)}>Remove</MenuItem>}
                          </Menu>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {data.total > data.rows.length && (
              <div className="mt-3 text-center text-xs text-ink-faint">
                Showing {data.rows.length} of {data.total} — use the filters or search to narrow it down.
              </div>
            )}
          </div>
        )}
      </div>
      </>}

      {/* Start confirmation — the consent tick in HealthSherpa is the AGENT's statement. */}
      <Drawer
        open={!!confirmRow}
        title="Start authorization"
        sub={confirmRow ? <><b className="text-ink">{confirmRow.first_name} {confirmRow.last_name}</b> · {fmtDob(confirmRow.dob)} · {confirmRow.state}</> : null}
        icon="🛡️"
        onClose={() => setConfirmRow(null)}
        footer={
          <>
            <button className={btnGhost} onClick={() => setConfirmRow(null)}>Cancel</button>
            <button className={btn} disabled={!consent || !!busyId}
                    onClick={() => { const r = confirmRow!; setConfirmRow(null); doStart(r); }}>
              Start in HealthSherpa
            </button>
          </>
        }
      >
        <div className="space-y-3 text-xs text-ink-soft">
          <ol className="list-decimal space-y-1 pl-4">
            <li>Keep HealthSherpa logged in (Chrome). If it asks you to log in, do it — the extension waits.</li>
            <li>The extension searches the Marketplace, picks <b>Plan Year 2027</b> and texts the client the HealthSherpa link.</li>
            <li>The client taps the link on <b>their own phone</b> and authorizes. The link lasts {LINK_MINUTES} minutes.</li>
            <li>When they finish, the extension clicks Finish and marks the client <b>Authorized</b>.</li>
          </ol>
          <label className="flex items-start gap-2 rounded-lg border border-hairline bg-black/5 p-3 text-ink">
            <input type="checkbox" className="mt-0.5" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>
              <b>I have this client's consent to work on their behalf</b> and they are expecting a text from HealthSherpa now.
              <span className="mt-1 block text-ink-muted">The extension ticks HealthSherpa's consent boxes in your name.</span>
            </span>
          </label>
          <p className="text-ink-muted">Never verify or authorize for the client, and never use your own phone or email.</p>
        </div>
      </Drawer>

      {editRow && <EditDrawer row={editRow} isManager={!!data?.is_manager} agents={agents}
                              onClose={() => setEditRow(null)}
                              onSaved={() => { setEditRow(null); load(); }} />}

      <Drawer open={!!historyRow} title="History"
              sub={historyRow ? `${historyRow.first_name} ${historyRow.last_name}` : ""}
              icon="🕘" onClose={() => setHistoryRow(null)}>
        {historyRow && (
          <div className="space-y-2 text-xs">
            {(historyRow.events || []).length === 0 && <div className="text-ink-muted">Nothing yet.</div>}
            {[...(historyRow.events || [])].reverse().map((ev, i) => (
              <div key={i} className="rounded-lg bg-black/5 px-3 py-2">
                <div className="flex justify-between gap-2">
                  <span className="font-bold text-ink">
                    {ev.status ? (STATUS[ev.status]?.label || ev.status) : (STEP_LABEL[ev.step || ""] || ev.step)}
                  </span>
                  <span className="tabular-nums text-ink-faint">{new Date(ev.at).toLocaleString()}</span>
                </div>
                {ev.detail && <div className={`mt-0.5 text-ink-muted ${ev.step === "diagnostic" ? "whitespace-pre-wrap break-words font-mono text-[0.62rem]" : ""}`}>{ev.detail}</div>}
              </div>
            ))}
          </div>
        )}
      </Drawer>

      <Drawer open={showInstall} title="Install the Chrome extension" icon="🧩" onClose={() => setShowInstall(false)}>
        <div className="space-y-3 text-xs text-ink-soft">
          <p>The extension runs on a Windows or Mac computer in <b>Google Chrome</b> (not on an iPad or phone).</p>
          <ol className="list-decimal space-y-1.5 pl-4">
            <li>Get the <b>iag-healthsherpa-extension.zip</b> file from your manager and unzip it.</li>
            <li>In Chrome open <code className="rounded bg-black/5 px-1">chrome://extensions</code>.</li>
            <li>Turn on <b>Developer mode</b> (top right).</li>
            <li>Click <b>Load unpacked</b> and choose the unzipped folder.</li>
            <li>Come back to this page and refresh it — the badge turns green.</li>
          </ol>
          <p className="text-ink-muted">Then log in to HealthSherpa in the same Chrome, as you normally do.</p>
        </div>
      </Drawer>
    </div>
  );
}

function RunningCard({ row, live, busy, maxResends, onStop }: {
  row: Row; live?: ExtUpdate; busy: boolean; maxResends: number; onStop: () => void;
}) {
  const step = live?.step || row.step || "start";
  const detail = live?.detail || row.status_detail;
  let countdown: string | null = null;
  if (row.status === "LINK_SENT" && row.link_sent_at) {
    // link_sent_at is the LATEST send (the server moves it on every resend),
    // and each send gives the client a fresh 15 minutes.
    const left = LINK_MINUTES * 60 - Math.floor((Date.now() - new Date(row.link_sent_at).getTime()) / 1000);
    countdown = left > 0 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "0:00";
  }
  return (
    <div className="glass rounded-2xl border-2 border-accent/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[0.68rem] font-bold uppercase tracking-wide text-accent">
            {row.auto && "Auto mode · "}{row.status === "LINK_SENT" ? "Waiting for the client" : "Running in HealthSherpa"}
          </div>
          <div className="truncate text-lg font-extrabold text-ink">{row.first_name} {row.last_name}</div>
          <div className="text-xs text-ink-muted">
            {live?.waiting_login
              ? "Please log in to HealthSherpa — the extension continues by itself."
              : (detail || STEP_LABEL[step] || step)}
          </div>
          {row.status === "LINK_SENT" && (
            <div className="mt-1 text-xs text-ink-soft">
              Ask the client to tap the HealthSherpa link on their phone and press <b>Authorize</b>.
              {row.link_sends > 1 && <> Link sent {row.link_sends}× (up to {maxResends + 1}).</>}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {countdown && (
            <div className="text-center">
              <div className="text-2xl font-extrabold tabular-nums text-ink">{countdown}</div>
              <div className="text-[0.62rem] uppercase tracking-wide text-ink-faint">link time left</div>
            </div>
          )}
          <button className={btnGhost} onClick={focusHealthSherpa}>Show HealthSherpa tab</button>
          <button className="rounded-lg border border-danger/40 px-3 py-1.5 text-xs font-bold text-danger hover:bg-danger/10 disabled:opacity-50"
                  disabled={busy} onClick={onStop}>Stop</button>
        </div>
      </div>
    </div>
  );
}

function UploadCard({ isManager, agents, onDone, onError }: {
  isManager: boolean; agents: Agent[]; onDone: (msg: string) => void; onError: (m: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [assign, setAssign] = useState("");
  const [busy, setBusy] = useState(false);

  const upload = async (file: File) => {
    setBusy(true);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", file.name);
    if (assign) fd.append("assign_to", assign);
    try {
      const r = await apiUpload<{ added: number; invalid: number; duplicates: number }>("/hs-auth/upload", fd);
      const parts = [`${r.added} client${r.added === 1 ? "" : "s"} added`];
      if (r.invalid) parts.push(`${r.invalid} need fixing (Fix data)`);
      if (r.duplicates) parts.push(`${r.duplicates} already in the list`);
      onDone(parts.join(" · "));
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const template = () => {
    const url = URL.createObjectURL(new Blob([TEMPLATE], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = "healthsherpa-clients-template.csv"; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="glass flex flex-wrap items-center justify-between gap-3 rounded-2xl p-4">
      <div>
        <h2 className="text-sm font-bold text-ink">Upload clients</h2>
        <p className="text-xs text-ink-muted">
          Excel (.xlsx) or CSV with <b>First Name, Last Name, Date of Birth, State</b> — Phone and Email optional.
          Names are cleaned and dates turned into MM/DD/YYYY for you.{" "}
          <button className="font-semibold text-accent underline" onClick={template}>Download template</button>
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {isManager && (
          <select className={inputCls} value={assign} onChange={(e) => setAssign(e.target.value)} title="Who works this list">
            <option value="">Any agent can take them</option>
            {agents.map((a) => <option key={a.id} value={a.id}>Give to {a.name}</option>)}
          </select>
        )}
        <input ref={fileRef} type="file" accept=".xlsx,.csv" className="hidden"
               onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
        <button className={btn} disabled={busy} onClick={() => fileRef.current?.click()}>
          {busy ? "Uploading…" : "Choose file"}
        </button>
      </div>
    </div>
  );
}

function EditDrawer({ row, isManager, agents, onClose, onSaved }: {
  row: Row; isManager: boolean; agents: Agent[]; onClose: () => void; onSaved: () => void;
}) {
  const [f, setF] = useState({
    first_name: row.first_name, last_name: row.last_name, dob: fmtDob(row.dob) === "—" ? "" : fmtDob(row.dob),
    state: row.state || "", phone: fmtPhone(row.phone), email: row.email || "", assigned_to: row.assigned_to || "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true); setErr(null);
    const body: Record<string, string | null> = {
      first_name: f.first_name, last_name: f.last_name, dob: f.dob, state: f.state, phone: f.phone, email: f.email,
    };
    if (isManager) body.assigned_to = f.assigned_to || null;
    try {
      const r = await api<Row>(`/hs-auth/requests/${row.id}`, { method: "PATCH", body: JSON.stringify(body) });
      if (r.status === "INVALID") setErr(r.status_detail || "Still not valid.");
      else onSaved();
    } catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  };
  return (
    <Drawer open title="Edit client" sub={row.status === "INVALID" ? row.status_detail : "Fix the details HealthSherpa searches with."}
            icon="✏️" onClose={onClose}
            footer={<>
              <button className={btnGhost} onClick={onClose}>Cancel</button>
              <button className={btn} disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
            </>}>
      {err && <div className="rounded-lg bg-danger/10 px-3 py-2 text-xs font-semibold text-danger">{err}</div>}
      <Field label="First name" required hint="Letters only — no periods or commas.">
        <input className={drawerCtl} value={f.first_name} onChange={(e) => setF({ ...f, first_name: e.target.value })} />
      </Field>
      <Field label="Last name" required>
        <input className={drawerCtl} value={f.last_name} onChange={(e) => setF({ ...f, last_name: e.target.value })} />
      </Field>
      <Field label="Date of birth" required hint="MM/DD/YYYY — the full 4-digit year.">
        <input className={`${drawerCtl} tabular-nums`} placeholder="MM/DD/YYYY" value={f.dob}
               onChange={(e) => setF({ ...f, dob: e.target.value })} />
      </Field>
      <Field label="Coverage state" required hint="2-letter code or full name (TX / Texas).">
        <input className={drawerCtl} value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })} />
      </Field>
      <Field label="Phone" hint="Optional. If HealthSherpa has a different number, the extension stops and asks you to check.">
        <input className={`${drawerCtl} tabular-nums`} value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
      </Field>
      <Field label="Email">
        <input className={drawerCtl} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
      </Field>
      {isManager && (
        <Field label="Agent">
          <select className={drawerCtl} value={f.assigned_to} onChange={(e) => setF({ ...f, assigned_to: e.target.value })}>
            <option value="">Any agent can take it</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </Field>
      )}
    </Drawer>
  );
}

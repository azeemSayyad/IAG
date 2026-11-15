/* HealthSherpa text campaigns — the HealthSherpa page's "Text campaigns" tab.
 *
 * COMPLETELY separate from the Auto Dialer's Text campaigns: replies here are
 * never called. Upload clients (name, DOB, state, phone) + one message -> Start.
 * A positive reply gets the approved follow-up ("What company are you
 * renewing?"); their answer queues a HealthSherpa job that the agent's AUTO
 * MODE runs; STOP / no at any point = Do-Not-Call. Everything the closer needs
 * is in the table + Export. Backend: app/hs_auth/campaigns.py.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, apiUpload, authedFetch } from "../../lib/api";
import { stopJob } from "../../lib/hsBridge";
import { Drawer, DrawerSection, Field, drawerCtl } from "../Drawer";
import { Empty, ago, errText, fmtDob, fmtPhone, useConfirm } from "./ui";
import { AutomationPanel, CHIPS, CHIP_LABEL, type HsStatus } from "./HsAutomation";
import { TemplatePicker, TextLength } from "./HsTemplatePicker";
import { previewText } from "./hsTemplates";

export type Counts = {
  total: number; queued: number; sent: number; replied: number; positive: number; answered: number;
  no_answer: number; link_sent: number; authorized: number; expired: number; dnc: number; invalid: number; failed: number;
  retext_ready: number; retexted: number; retext_queued: number;
};
export type HsCampaign = {
  id: string; name: string; template: string; status: string; pause_reason: string | null;
  file_name: string | null; hs_agent_id: string; hs_agent_name: string | null;
  rows_in_file: number; skipped_invalid: number; skipped_no_phone: number; skipped_duplicate: number; skipped_dnc: number;
  answer_wait_minutes: number; reminder_enabled: boolean; reminder_text: string | null; reminder_minutes: number;
  expire_minutes: number; resend_on_expiry: boolean;
  created_at: string | null; started_at: string | null; finished_at: string | null; archived_at?: string | null;
  counts: Partial<Counts>; preview: string;
  /** This campaign's follow-up after a YES (the approved text unless edited). */
  followup_text?: string; followup_custom?: boolean;
};
export type HsOverview = {
  campaigns: HsCampaign[]; can_manage: boolean; is_hs_agent: boolean; my_queue: number;
  usage: { used: number; limit: number; left: number };
  followup_text: string; blast_default: string; me: string; reminder_draft: string; stages: Record<string, string>;
  sent_today: number; daily_cap: number; simulating: boolean; needs_reply: number;
  /** How many of our sending numbers each state has: {"TX": 2, "FL": 1}. */
  state_numbers: Record<string, number>; retext_max: number;
};
type Text = {
  id: string; direction: "IN" | "OUT"; kind: string; body: string | null; status: string; sent_by_name: string | null;
  reply_kind: string | null; from_number: string | null; error: string | null; at: string | null;
};
export type Client = {
  id: string; first_name: string; last_name: string; dob: string | null; state: string | null; phone: string | null;
  email: string | null; status: string; status_detail: string | null; step: string | null; hs_app_id: string | null;
  link_sends: number; runs: number; started_at: string | null; link_sent_at: string | null; authorized_at: string | null;
  finished_at: string | null; updated_at: string | null; created_at: string | null; row_number: number | null;
  batch_id: string | null; assigned_to: string | null; assigned_name: string | null; started_by_name: string | null;
  campaign_id: string | null; campaign_name: string | null; convo: string | null; convo_detail: string | null;
  stage: string; yes_text: string | null; yes_at: string | null; followup_sent_at: string | null;
  renewing_company: string | null; answered_at: string | null; reminder_sent_at: string | null; last_text_at: string | null;
  needs_reply: boolean; can_text?: boolean; texting_hours_ok?: boolean; from_number?: string | null;
  last_text?: { direction: string; kind: string; body: string | null; at: string } | null;
  texts?: Text[]; events?: { at: string; step: string | null; status: string | null; detail: string }[];
};
type ClientsResp = { rows: Client[]; total: number; counts: Record<string, number> };
type Agent = { id: string; name: string; role: string };

const STAGE_CLS: Record<string, string> = {
  NEEDS_REPLY: "bg-danger text-white",
  QUEUED: "bg-black/5 text-ink-muted", TEXTED: "bg-accent/10 text-accent", ASKED: "bg-pending/15 text-pending",
  NO_ANSWER: "bg-black/5 text-ink-soft", HS_QUEUED: "bg-accent/10 text-accent", HS_RUNNING: "bg-accent text-white",
  LINK_SENT: "bg-pending/15 text-pending", AUTHORIZED: "bg-success/15 text-success", UNCONFIRMED: "bg-pending/15 text-pending", EXPIRED: "bg-danger/10 text-danger",
  HS_REVIEW: "bg-pending/15 text-pending", DNC: "bg-danger/10 text-danger", NOT_SENT: "bg-black/5 text-ink-muted",
  INVALID: "bg-danger/10 text-danger",
};
const STAGE_ORDER = ["ALL", "NEEDS_REPLY", "QUEUED", "TEXTED", "ASKED", "NO_ANSWER", "HS_QUEUED", "HS_RUNNING", "LINK_SENT",
  "AUTHORIZED", "UNCONFIRMED", "EXPIRED", "HS_REVIEW", "DNC", "NOT_SENT", "INVALID"];
// Each campaign state has its own colour so the list reads at a glance.
const CAMP_PILL: Record<string, string> = {
  DRAFT: "bg-black/5 text-ink-muted", RUNNING: "bg-success text-white", PAUSED: "bg-pending text-white",
  COMPLETED: "bg-accent text-white", STOPPED: "bg-black/10 text-ink-muted",
};
const CAMP_ROW: Record<string, { box: string; bar: string }> = {
  RUNNING: { box: "border-success/40 bg-success/[0.06]", bar: "bg-success" },
  PAUSED: { box: "border-pending/40 bg-pending/[0.07]", bar: "bg-pending" },
  COMPLETED: { box: "border-accent/30 bg-accent/[0.04]", bar: "bg-accent" },
  STOPPED: { box: "border-hairline bg-black/[0.02] opacity-80", bar: "bg-ink-faint" },
  DRAFT: { box: "border-dashed border-ink-faint/60 bg-transparent", bar: "bg-transparent" },
};
const CAMP_LABEL: Record<string, string> = {
  DRAFT: "Draft", RUNNING: "Sending", PAUSED: "Paused", COMPLETED: "All texted", STOPPED: "Stopped",
};
const KIND_LABEL: Record<string, string> = {
  BLAST: "Campaign text", FOLLOWUP: "Follow-up", REMINDER: "Reminder", MANUAL: "You", REPLY: "Client", RETEXT: "Re-text",
};

const btn = "rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white hover:bg-accent-hover disabled:opacity-50";
const btnGhost = "rounded-lg border border-hairline px-3 py-1.5 text-xs font-semibold text-ink-muted hover:bg-black/5 hover:text-ink disabled:opacity-50";
const inputCls = "rounded-lg border border-hairline bg-white px-2 py-1.5 text-xs text-ink";

const TEMPLATE_CSV = "First Name,Last Name,Date of Birth,State,Phone\nJane,Doe,01/31/1985,FL,(555) 123-4567\n";

export default function HsCampaigns({ overview, reloadOverview, agents, status, statusAt, onEdit, onError, onNote, onRunning }: {
  overview: HsOverview; reloadOverview: () => void; agents: Agent[];
  /** The automation's full picture (HsAutomation.tsx), polled by the page. */
  status: HsStatus | null; statusAt: number;
  onEdit: (c: Client) => void; onError: (m: string) => void; onNote: (m: string) => void;
  /** A campaign was started from here — the page turns on auto mode if it's this user's. */
  onRunning: (c: HsCampaign) => void;
}) {
  const [campaignF, setCampaignF] = useState("");
  const [stage, setStage] = useState("ALL");
  const [q, setQ] = useState("");
  const [data, setData] = useState<ClientsResp | null>(null);
  const [creating, setCreating] = useState(false);
  const [settingsFor, setSettingsFor] = useState<HsCampaign | null>(null);
  const [retextFor, setRetextFor] = useState<HsCampaign | null>(null);
  const [detail, setDetail] = useState<Client | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [confirm, confirmEl] = useConfirm();
  const [showArchived, setShowArchived] = useState(false);
  const [listStage, setListStage] = useState<string | null>(null);  // a status-panel chip's drawer
  const [addingOne, setAddingOne] = useState(false);
  const active = overview.campaigns.filter((c) => !c.archived_at);
  const archivedN = overview.campaigns.length - active.length;

  const params = useCallback(() => {
    const p = new URLSearchParams();
    if (campaignF) p.set("campaign", campaignF);
    if (stage !== "ALL") p.set("stage", stage);
    if (q.trim()) p.set("q", q.trim());
    return p;
  }, [campaignF, stage, q]);

  // The table loads PAGE clients at a time; scrolling to the bottom loads the next
  // PAGE. The 5-s refresh re-reads only the top of what's loaded (≤ REFRESH_MAX) so a
  // long scrolled list isn't re-fetched every time; deeper rows refresh on the next page.
  const PAGE = 30, REFRESH_MAX = 300;
  const [rows, setRows] = useState<Client[]>([]);
  const rowsRef = useRef<Client[]>([]);
  rowsRef.current = rows;
  const [loadingMore, setLoadingMore] = useState(false);
  const moreRef = useRef(false);
  const scrollBox = useRef<HTMLDivElement>(null);

  // The parent passes a new onError function on every render (it re-renders every
  // 5 s) — keep it in a ref so it doesn't look like a filter change and reset the list.
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const load = useCallback((reset = false) => {
    const p = params();
    const n = reset ? PAGE : Math.min(Math.max(rowsRef.current.length, PAGE), REFRESH_MAX);
    p.set("limit", String(n));
    return api<ClientsResp>(`/hs-auth/campaigns/clients?${p}`).then((r) => {
      setData(r);
      setRows((prev) => {
        if (reset) return r.rows;
        const seen = new Set(r.rows.map((x) => x.id));
        return [...r.rows, ...prev.slice(n).filter((x) => !seen.has(x.id))];
      });
    }).catch((e) => onErrorRef.current(errText(e)));
  }, [params]);

  const loadMore = useCallback(() => {
    if (moreRef.current || !data || rowsRef.current.length >= data.total) return;
    moreRef.current = true; setLoadingMore(true);
    const p = params();
    p.set("limit", String(PAGE)); p.set("offset", String(rowsRef.current.length));
    api<ClientsResp>(`/hs-auth/campaigns/clients?${p}`).then((r) => {
      setRows((prev) => { const seen = new Set(prev.map((x) => x.id)); return [...prev, ...r.rows.filter((x) => !seen.has(x.id))]; });
    }).catch((e) => onErrorRef.current(errText(e)))
      .finally(() => { moreRef.current = false; setLoadingMore(false); });
  }, [data, params]);

  // Filters / search changed: start again from the first page.
  useEffect(() => { const t = window.setTimeout(() => load(true), q ? 300 : 0); return () => window.clearTimeout(t); }, [load, q]);
  useEffect(() => { const t = window.setInterval(() => load(false), 5000); return () => window.clearInterval(t); }, [load]);
  // Load the next page when the rows' own scroll box nears its bottom (and right
  // away if the loaded rows don't even fill the box).
  const nearBottom = useCallback(() => {
    const b = scrollBox.current;
    if (b && b.scrollTop + b.clientHeight >= b.scrollHeight - 300) loadMore();
  }, [loadMore]);
  useEffect(() => { nearBottom(); }, [nearBottom, rows.length]);

  // Keep an open detail drawer live (new texts / status).
  useEffect(() => {
    if (!detail) return;
    const t = window.setInterval(() => {
      api<Client>(`/hs-auth/campaigns/clients/${detail.id}`).then(setDetail).catch(() => {});
    }, 5000);
    return () => window.clearInterval(t);
  }, [detail?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (c: HsCampaign, action: string) => {
    if (action === "stop" && !(await confirm({
      title: `Stop “${c.name}”?`, icon: "⏹", tone: "danger", confirmLabel: "Stop campaign",
      points: ["Nobody still waiting gets the first text.", "Clients already talking to us carry on (follow-ups, HealthSherpa links)."],
    }))) return;
    if (action === "delete" && c.status === "DRAFT" && !(await confirm({
      title: `Delete the draft “${c.name}”?`, tone: "danger", confirmLabel: "Delete draft",
      body: "Nothing was texted yet. The draft and its client list are deleted." }))) return;
    if (action === "delete" && c.status !== "DRAFT" && !(await confirm({
      title: `Remove “${c.name}”?`, tone: "danger", confirmLabel: "Remove campaign",
      body: <>It and its <b>{c.counts.total ?? 0} clients</b> leave this page.</>,
      points: [
        "Nothing more is texted, and anyone queued or running in HealthSherpa is cancelled.",
        "Opt-outs stay on Do-Not-Call.",
        "If one of these clients replies later, the text is saved but nothing is sent back — and they're never called.",
      ],
      note: "This can't be undone. Export to Excel first if you need the list — or use Archive to just hide it.",
    }))) return;
    setBusy(c.id);
    try {
      if (action === "delete") await api(`/hs-auth/campaigns/${c.id}`, { method: "DELETE" });
      else await api(`/hs-auth/campaigns/${c.id}/${action}`, { method: "POST" });
      if (action === "start" || action === "resume") onRunning(c);
    } catch (e) { onError(errText(e)); }
    finally { setBusy(null); reloadOverview(); load(); }
  };

  const openDetail = (id: string) => api<Client>(`/hs-auth/campaigns/clients/${id}`).then(setDetail).catch((e) => onError(errText(e)));

  const requeue = async (c: Client) => {
    try { await api(`/hs-auth/requests/${c.id}/reset`, { method: "POST" }); openDetail(c.id); load(); }
    catch (e) { onError(errText(e)); }
  };
  const stopRunning = async (c: Client) => {
    stopJob(c.id);
    try { await api(`/hs-auth/requests/${c.id}/cancel`, { method: "POST" }); openDetail(c.id); load(); }
    catch (e) { onError(errText(e)); }
  };

  const doExport = async () => {
    setExporting(true);
    try {
      const res = await authedFetch(`/hs-auth/campaigns/export?${params()}`);
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url; a.download = `healthsherpa-clients-${new Date().toISOString().slice(0, 10)}.csv`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { onError(errText(e)); }
    finally { setExporting(false); }
  };

  const stages = overview.stages;
  return (
    <div className="space-y-4" data-testid="hs-campaigns">
      {status && overview.campaigns.length > 0 && (
        <AutomationPanel st={status} at={statusAt} onOpen={openDetail} onList={setListStage} />
      )}
      <section className="glass rounded-2xl p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-2xl">
            <h2 className="text-sm font-bold text-ink">HealthSherpa text campaigns</h2>
            <p className="text-xs text-ink-muted">
              Text a list → clients who say yes are asked which company they're renewing → <b>auto mode</b> sends
              the HealthSherpa link. Never called. STOP or “no” = Do-Not-Call.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {overview.needs_reply > 0 && (
              <button onClick={() => { setStage("NEEDS_REPLY"); setCampaignF(""); }} data-testid="hs-needs-reply-badge"
                      className="rounded-full bg-danger px-3 py-1 text-xs font-bold text-white hover:opacity-90">
                💬 {overview.needs_reply} need{overview.needs_reply === 1 ? "s" : ""} a reply
              </button>
            )}
            <span className="rounded-full bg-black/5 px-3 py-1 text-xs font-bold tabular-nums text-ink-soft"
                  title="Every text from our numbers today (all campaigns) — carrier daily cap">
              Texts today: {overview.sent_today.toLocaleString()} / {overview.daily_cap.toLocaleString()}
            </span>
            {archivedN > 0 && (
              <button type="button" onClick={() => setShowArchived(!showArchived)} data-testid="hs-archived-toggle"
                      title="Archived campaigns are only hidden — they keep working in the background"
                      className={`rounded-full px-3 py-1 text-xs font-bold ${showArchived ? "bg-ink text-white" : "bg-black/5 text-ink-soft hover:text-ink"}`}>
                🗂️ {showArchived ? "Hide archived" : "Show archived"} ({archivedN})
              </button>
            )}
            {overview.can_manage && (
              <button className={btnGhost} onClick={() => setAddingOne(true)} data-testid="hs-add-one">+ Text one client</button>
            )}
            {overview.can_manage && <button className={btn} onClick={() => setCreating(true)}>New campaign</button>}
          </div>
        </div>

        {overview.campaigns.length === 0 ? (
          <Empty>No HealthSherpa campaigns yet.{overview.can_manage ? " Press New campaign to upload a list." : ""}</Empty>
        ) : (
          <div className="mt-3 space-y-2">
            {active.length === 0 && !showArchived && (
              <div className="py-4 text-center text-xs text-ink-muted">All campaigns are archived — they keep working in the background.</div>
            )}
            {(showArchived ? overview.campaigns : active).map((c) => (
              <CampaignRow key={c.id} c={c} canManage={overview.can_manage} busy={busy === c.id}
                           onAction={(a) => act(c, a)} onSettings={() => setSettingsFor(c)} onRetext={() => setRetextFor(c)}
                           onShow={() => { setCampaignF(c.id); setStage("ALL"); }} />
            ))}
          </div>
        )}
      </section>

      <section className="glass flex flex-col rounded-2xl p-4" style={{ height: "calc(100vh - 96px)", minHeight: 420 }} data-testid="hs-clients">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-bold text-ink">Clients</h2>
          <button className={btnGhost} disabled={exporting} onClick={doExport}
                  title="Everything below (with these filters) as a spreadsheet — opens in Excel">
            {exporting ? "Exporting…" : "⬇ Export to Excel (CSV)"}
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {STAGE_ORDER.map((key) => {
            const n = data?.counts?.[key] || 0;
            if (key !== "ALL" && !n && stage !== key) return null;
            return (
              <button key={key} onClick={() => setStage(key)}
                      className={`rounded-full px-3 py-1 text-xs font-semibold ${stage === key ? "bg-accent text-white"
                        : key === "NEEDS_REPLY" ? "bg-danger/10 text-danger hover:bg-danger/15" : "bg-black/5 text-ink-muted hover:text-ink"}`}>
                {key === "ALL" ? "All" : stages[key] || key} <span className="tabular-nums opacity-70">{n}</span>
              </button>
            );
          })}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input className={`${inputCls} w-64`} placeholder="Search name, phone, company, app ID…" value={q}
                 onChange={(e) => setQ(e.target.value)} />
          <select className={`${inputCls} max-w-64`} value={campaignF} onChange={(e) => setCampaignF(e.target.value)}>
            <option value="">All campaigns</option>
            {overview.campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>

        {!data ? <Empty>Loading…</Empty> : rows.length === 0 ? (
          <Empty>{overview.campaigns.length ? "Nothing matches these filters." : "Clients show up here once a campaign is uploaded."}</Empty>
        ) : (
          <div ref={scrollBox} onScroll={nearBottom} className="mt-3 min-h-0 flex-1 overflow-auto" data-testid="hs-clients-scroll">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="sticky top-0 z-10 bg-white">
                <tr className="border-b border-hairline text-[0.68rem] uppercase tracking-wide text-ink-faint">
                  <th className="py-2 pr-3 font-bold">Client</th>
                  <th className="py-2 pr-3 font-bold">DOB · State</th>
                  <th className="py-2 pr-3 font-bold">Last text</th>
                  <th className="py-2 pr-3 font-bold">Company renewing</th>
                  <th className="py-2 pr-3 font-bold">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline-soft">
                {rows.map((r) => (
                  <tr key={r.id} className="cursor-pointer align-top hover:bg-black/[0.02]" onClick={() => openDetail(r.id)}>
                    <td className="py-2.5 pr-3">
                      <div className="flex items-center gap-1.5 font-bold text-ink">
                        {r.needs_reply && <span className="h-2 w-2 shrink-0 rounded-full bg-danger" title="Needs a reply" />}
                        {r.first_name} {r.last_name}
                      </div>
                      <div className="tabular-nums text-ink-faint">{fmtPhone(r.phone)}</div>
                      {!campaignF && <div className="text-[0.66rem] text-ink-faint">{r.campaign_name}</div>}
                    </td>
                    <td className="py-2.5 pr-3 tabular-nums text-ink-soft">{fmtDob(r.dob)} · {r.state || "—"}</td>
                    <td className="max-w-[260px] py-2.5 pr-3">
                      {r.last_text ? (
                        <>
                          <div className="truncate text-ink-soft">
                            <span className={`font-semibold ${r.last_text.direction === "IN" ? "text-ink" : "text-ink-muted"}`}>
                              {r.last_text.direction === "IN" ? "Client: " : `${r.last_text.kind === "MANUAL" ? "You" : KIND_LABEL[r.last_text.kind] || "Us"}: `}
                            </span>
                            {r.last_text.body}
                          </div>
                          <div className="text-[0.66rem] text-ink-faint">{ago(r.last_text.at)}</div>
                        </>
                      ) : <span className="text-ink-faint">—</span>}
                    </td>
                    <td className="py-2.5 pr-3 text-ink">{r.renewing_company || <span className="text-ink-faint">—</span>}</td>
                    <td className="max-w-[260px] py-2.5 pr-3">
                      <span className={`inline-block rounded-full px-2 py-0.5 text-[0.68rem] font-bold ${STAGE_CLS[r.stage] || STAGE_CLS.QUEUED}`}>
                        {stages[r.stage] || r.stage}
                      </span>
                      <StageDetail c={r} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-3 text-center text-xs text-ink-faint" data-testid="hs-clients-more">
              {rows.length < data.total
                ? (loadingMore ? "Loading more…" : <button className="underline" onClick={loadMore}>Showing {rows.length} of {data.total} — load more</button>)
                : data.total > PAGE ? `All ${data.total} clients shown` : null}
            </div>
          </div>
        )}
      </section>

      {creating && <NewCampaignDrawer overview={overview} agents={agents} onClose={() => setCreating(false)}
                                      onCreated={(c, msg, started) => {
                                        setCreating(false); onNote(msg); reloadOverview(); setCampaignF(c.id);
                                        if (started) onRunning(c);
                                      }} />}
      {retextFor && <RetextDrawer c={retextFor} max={overview.retext_max} onClose={() => setRetextFor(null)}
                                  onSent={(msg) => { setRetextFor(null); onNote(msg); reloadOverview(); load(); }} />}
      {settingsFor && <SettingsDrawer c={settingsFor} approvedFollowup={overview.followup_text} onClose={() => setSettingsFor(null)}
                                      onSaved={() => { setSettingsFor(null); reloadOverview(); }} />}
      {confirmEl}
      {addingOne && <AddClientDrawer overview={overview} agents={agents} onClose={() => setAddingOne(false)}
                                     onAdded={(r) => { setAddingOne(false); onNote(r.note); reloadOverview(); load(true); openDetail(r.id); }} />}
      {listStage && <StageListDrawer stage={listStage} onClose={() => setListStage(null)} onOpen={openDetail} onError={onError} />}
      {detail && <ClientDrawer c={detail} stages={stages} onClose={() => setDetail(null)}
                               onChanged={(d) => { setDetail(d); load(); reloadOverview(); }}
                               onEdit={() => { onEdit(detail); setDetail(null); }}
                               onRequeue={() => requeue(detail)} onStop={() => stopRunning(detail)} />}
    </div>
  );
}

function StageDetail({ c }: { c: Client }) {
  const line = (() => {
    switch (c.stage) {
      case "AUTHORIZED": return c.authorized_at ? `Authorized ${new Date(c.authorized_at).toLocaleString()}` : null;
      case "HS_QUEUED": case "HS_RUNNING": case "LINK_SENT": case "UNCONFIRMED": case "EXPIRED": case "HS_REVIEW": return c.status_detail;
      case "ASKED": return c.followup_sent_at ? `Asked ${ago(c.followup_sent_at)}` : "Sending the follow-up…";
      default: return c.convo_detail;
    }
  })();
  return (
    <>
      {line && <div className="mt-1 text-[0.7rem] leading-snug text-ink-muted">{line}</div>}
      {c.hs_app_id && <div className="text-[0.66rem] text-ink-faint">App ID {c.hs_app_id}</div>}
    </>
  );
}

function CampaignRow({ c, canManage, busy, onAction, onSettings, onShow, onRetext }: {
  c: HsCampaign; canManage: boolean; busy: boolean; onAction: (a: string) => void; onSettings: () => void; onShow: () => void;
  onRetext: () => void;
}) {
  const n = c.counts || {};
  // The funnel, left to right. The rest is one quiet line underneath.
  const stats: [string, number | undefined][] = [
    ["Texted", n.sent], ["Replied", n.replied], ["Answered", n.answered],
    ["Link sent", n.link_sent], ["Authorized", n.authorized], ["Opted out", n.dnc],
  ];
  const extra = [
    n.no_answer ? `${n.no_answer} no answer yet` : "", n.expired ? `${n.expired} expired` : "",
    n.invalid ? `${n.invalid} need fixing` : "", n.failed ? `${n.failed} not delivered` : "",
    n.retexted ? `${n.retexted} re-texted` : "", n.retext_queued ? `${n.retext_queued} re-texts waiting to send` : "",
  ].filter(Boolean).join(" · ");
  return (
    <div className={`relative rounded-xl border p-3 pl-4 ${(CAMP_ROW[c.status] || CAMP_ROW.STOPPED).box}`}
         data-testid="hs-campaign-row" data-status={c.status}>
      <span className={`absolute inset-y-0 left-0 w-1.5 rounded-l-xl ${(CAMP_ROW[c.status] || CAMP_ROW.STOPPED).bar}`} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <button className="truncate text-sm font-bold text-ink hover:underline" onClick={onShow}>{c.name}</button>
            <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.66rem] font-bold ${CAMP_PILL[c.status] || CAMP_PILL.DRAFT}`}>
              {c.status === "RUNNING" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" />}
              {CAMP_LABEL[c.status] || c.status}
            </span>
            {c.archived_at && <span className="rounded-full bg-black/5 px-2 py-0.5 text-[0.66rem] font-bold text-ink-muted">🗂️ Archived</span>}
          </div>
          <div className="text-[0.7rem] text-ink-faint">
            HealthSherpa agent: <b className="text-ink-soft">{c.hs_agent_name || "—"}</b>
            {" · "}{(n.total ?? 0).toLocaleString()} clients
            {(n.queued ?? 0) > 0 && <> · {n.queued} not texted yet</>}
            {c.created_at && <> · created {new Date(c.created_at).toLocaleDateString()}</>}
          </div>
        </div>
        {canManage && (
          <div className="flex flex-wrap gap-1">
            {c.status === "DRAFT" && <button className={btn} disabled={busy} onClick={() => onAction("start")}>▶ Run campaign</button>}
            {c.status === "RUNNING" && <button className={btnGhost} disabled={busy} onClick={() => onAction("pause")}>Pause</button>}
            {c.status === "PAUSED" && <button className={btn} disabled={busy} onClick={() => onAction("resume")}>Resume</button>}
            {(c.status === "RUNNING" || c.status === "PAUSED") && (
              <button className={btnGhost} disabled={busy} onClick={() => onAction("stop")}>Stop</button>
            )}
            {c.status !== "DRAFT" && (n.retext_ready ?? 0) > 0 && (
              <button className={btnGhost} disabled={busy} onClick={onRetext} data-testid="hs-retext"
                      title="Text again everyone who never replied">
                ↻ Re-text no-replies ({n.retext_ready})
              </button>
            )}
            <button className={btnGhost} onClick={onSettings}>Settings</button>
            {c.status !== "DRAFT" && (c.archived_at ? (
              <button className={btnGhost} disabled={busy} onClick={() => onAction("unarchive")} data-testid="hs-unarchive">
                Unarchive
              </button>
            ) : (
              <span className="inline-flex items-center">
                <button className={btnGhost} disabled={busy} onClick={() => onAction("archive")} data-testid="hs-archive">
                  🗂️ Archive
                </button>
                <InfoTip text="Archive only hides this campaign from the list to keep the page clean. Everything keeps working — texts, replies, follow-ups, HealthSherpa links — and its clients stay in the table. Bring it back any time from “Archived”." />
              </span>
            ))}
            <button className={`${btnGhost} hover:!border-danger/40 hover:!text-danger`} disabled={busy}
                    onClick={() => onAction("delete")} data-testid="hs-remove"
                    title={c.status === "DRAFT" ? "Delete this draft" : "End this campaign and take it off the page"}>
              {c.status === "DRAFT" ? "Delete" : "Remove"}
            </button>
          </div>
        )}
      </div>
      {c.status === "PAUSED" && c.pause_reason && <div className="mt-2 rounded-lg bg-pending/10 px-3 py-1.5 text-xs font-semibold text-pending">{c.pause_reason}</div>}
      <div className="mt-2 grid grid-cols-3 gap-1.5 sm:grid-cols-6">
        {stats.map(([label, v]) => (
          <div key={label} className={`rounded-lg px-2 py-1.5 text-center ${label === "Authorized" ? "bg-success/10" : "bg-black/5"}`}>
            <div className={`text-sm font-extrabold tabular-nums ${label === "Authorized" ? "text-success" : "text-ink"}`}>{(v ?? 0).toLocaleString()}</div>
            <div className="text-[0.62rem] uppercase tracking-wide text-ink-faint">{label}</div>
          </div>
        ))}
      </div>
      {extra && <div className="mt-1.5 text-[0.7rem] text-ink-faint">{extra}</div>}
    </div>
  );
}

function NewCampaignDrawer({ overview, agents, onClose, onCreated }: {
  overview: HsOverview; agents: Agent[]; onClose: () => void;
  onCreated: (c: HsCampaign, msg: string, started: boolean) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [agent, setAgent] = useState("");
  const [template, setTemplate] = useState(overview.blast_default);
  const [followup, setFollowup] = useState(overview.followup_text);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [check, setCheck] = useState<FileCheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const preview = previewText(template);

  // Check the file the moment it's chosen: mandatory columns + every row (nothing is saved).
  const pickFile = async (f: File | null) => {
    setFile(f); setCheck(null); setErr(null);
    if (!f) return;
    setChecking(true);
    const fd = new FormData();
    fd.append("file", f);
    try { setCheck(await apiUpload<FileCheckResult>("/hs-auth/campaigns/check", fd)); }
    catch (e) { setErr(errText(e)); }
    finally { setChecking(false); }
  };
  const fileOk = !!check?.ok && !checking;

  // run=true: ONE button — upload, start texting, and (on the HealthSherpa
  // agent's own computer) switch auto mode on. run=false: keep it as a draft.
  const save = async (run: boolean) => {
    if (!file) { setErr("Choose the client file first."); return; }
    if (!fileOk) { setErr(check?.missing.length ? `The file is missing: ${check.missing.join(", ")}.` : "Nobody in this file can be texted — fix the file first."); return; }
    setBusy(true); setErr(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name || file.name.replace(/\.[^.]+$/, ""));
    fd.append("template", template);
    fd.append("followup_text", followup);
    if (agent) fd.append("hs_agent_id", agent);
    try {
      const c = await apiUpload<HsCampaign>("/hs-auth/campaigns", fd);
      const skipped = [
        c.skipped_invalid && `${c.skipped_invalid} need fixing (Fix data — not texted until fixed)`,
        c.skipped_no_phone && `${c.skipped_no_phone} without a mobile number`,
        c.skipped_duplicate && `${c.skipped_duplicate} duplicate numbers`,
        c.skipped_dnc && `${c.skipped_dnc} on Do-Not-Call`,
      ].filter(Boolean);
      const left = skipped.length ? ` · ${skipped.join(" · ")}` : "";
      if (!run) {
        onCreated(c, `Campaign “${c.name}” saved as a draft with ${c.counts.total ?? 0} clients${left}. Press Start to send.`, false);
        return;
      }
      try {
        const started = await api<HsCampaign>(`/hs-auth/campaigns/${c.id}/start`, { method: "POST" });
        onCreated(started, `Campaign “${c.name}” is running — texting ${c.counts.total ?? 0} clients${left}.`, true);
      } catch (e) {
        onCreated(c, `Campaign “${c.name}” was uploaded but couldn't start: ${errText(e)} It's saved as a draft.`, false);
      }
    } catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  };

  const downloadTemplate = () => {
    const url = URL.createObjectURL(new Blob([TEMPLATE_CSV], { type: "text/csv" }));
    const a = document.createElement("a"); a.href = url; a.download = "healthsherpa-campaign-template.csv"; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Drawer open title="New HealthSherpa campaign" icon="💬" onClose={onClose}
            sub="Replies are never called — they go to HealthSherpa, not the Auto Dialer."
            footer={<>
              <button className={btnGhost} onClick={onClose}>Cancel</button>
              <button className={btnGhost} disabled={busy || !fileOk} onClick={() => save(false)}>Save as draft</button>
              <button className={btn} disabled={busy || !fileOk} onClick={() => save(true)} data-testid="hs-run-campaign"
                      title={!file ? "Choose the client file first" : !fileOk ? "Fix the file first (see the check above)" : undefined}>
                {busy ? "Starting…" : "▶ Run campaign"}
              </button>
            </>}>
      {err && <div className="rounded-lg bg-danger/10 px-3 py-2 text-xs font-semibold text-danger">{err}</div>}
      <Field label="Client file" required
             hint="Excel or CSV with First Name, Last Name, Date of Birth, State and Phone. Names are cleaned, dates become MM/DD/YYYY.">
        <input type="file" accept=".xlsx,.csv" className={drawerCtl} data-testid="hs-campaign-file"
               onChange={(e) => pickFile(e.target.files?.[0] || null)} />
      </Field>
      {checking && <div className="rounded-lg bg-black/5 px-3 py-2 text-xs text-ink-muted">Checking the file…</div>}
      {check && <FileCheck r={check} />}
      <button type="button" className="-mt-2 text-xs font-semibold text-accent underline" onClick={downloadTemplate}>
        Download a template
      </button>
      <Field label="Campaign name">
        <input className={drawerCtl} value={name} placeholder={file?.name.replace(/\.[^.]+$/, "") || "e.g. November renewals"}
               onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="HealthSherpa agent" hint="Whose HealthSherpa account sends the links. Clients authorize THIS agent, so the same agent must close the sale. Their portal tab runs auto mode.">
        <select className={drawerCtl} value={agent} onChange={(e) => setAgent(e.target.value)}>
          <option value="">Me</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>
      <TemplatePicker value={template} onPick={setTemplate} />
      <Field label="First text (the blast)" required hint="[Name] or {name} = their first name. “Reply STOP to opt out.” is added if missing. Pick a text above or write your own — you can edit it here.">
        <textarea className={`${drawerCtl} min-h-24`} value={template} maxLength={480} onChange={(e) => setTemplate(e.target.value)}
                  data-testid="hs-blast-text" />
        <div className="mt-1"><TextLength text={template} /></div>
      </Field>
      <FollowupField value={followup} approved={overview.followup_text} onChange={setFollowup} />
      <SendingNumbers byState={overview.state_numbers} />
      <DrawerSection icon="💬" title="What they get">
        <Bubble who="Campaign text" body={preview} />
        <Bubble who={`If they reply positively — follow-up${followup.trim() === overview.followup_text.trim() ? " (approved wording)" : " (edited)"}`}
                body={previewText(followup)} />
        <p className="text-xs text-ink-muted">
          Their answer (company name, “ok”, …) queues them for HealthSherpa. No answer in 10 minutes = “No answer yet”;
          a later answer still queues them. After the link: wait up to 10 minutes, then the next client (“Check in HealthSherpa”).
          The 5-minute reminder is OFF until the wording is approved (Settings).
        </p>
      </DrawerSection>
    </Drawer>
  );
}

/** Type in ONE client: texted now (in their texting hours), then the same flow as a
 * campaign client — YES → follow-up → company → HealthSherpa. */
function AddClientDrawer({ overview, agents, onClose, onAdded }: {
  overview: HsOverview; agents: Agent[]; onClose: () => void;
  onAdded: (r: { id: string; note: string; campaign_name: string }) => void;
}) {
  const camps = overview.campaigns.filter((c) => c.status !== "DRAFT" && c.name !== "Manual entries");
  const [campaign, setCampaign] = useState("");          // "" = the shared "Manual entries" campaign
  const [agent, setAgent] = useState("");
  const [f, setF] = useState({ first_name: "", last_name: "", dob: "", state: "", phone: "", email: "" });
  const manualCamp = overview.campaigns.find((c) => c.name === "Manual entries");
  const templateOf = (id: string) => overview.campaigns.find((c) => c.id === id)?.template || manualCamp?.template || overview.blast_default;
  const [message, setMessage] = useState(() => templateOf(""));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pick = (id: string) => { setCampaign(id); setMessage(templateOf(id)); };
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const ready = f.first_name.trim() && f.last_name.trim() && f.dob.trim() && f.state.trim() && f.phone.trim() && message.trim();
  const followup = overview.campaigns.find((c) => c.id === campaign)?.followup_text || manualCamp?.followup_text || overview.followup_text;
  const send = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api<{ id: string; note: string; campaign_name: string }>("/hs-auth/campaigns/add-client", {
        method: "POST",
        body: JSON.stringify({ ...f, message, campaign_id: campaign || null, hs_agent_id: campaign ? null : agent || null }),
      });
      onAdded(r);
    } catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  };
  return (
    <Drawer open title="Text one client" icon="✍️" onClose={onClose}
            sub="Type their details — they get the text, and their reply works exactly like a campaign client's."
            footer={<>
              <button className={btnGhost} onClick={onClose}>Cancel</button>
              <button className={btn} disabled={busy || !ready} onClick={send} data-testid="hs-add-one-send">
                {busy ? "Sending…" : "Send text"}
              </button>
            </>}>
      {err && <div className="rounded-lg bg-danger/10 px-3 py-2 text-xs font-semibold text-danger" data-testid="hs-add-one-error">{err}</div>}
      <div className="grid grid-cols-2 gap-3">
        <Field label="First name" required><input className={drawerCtl} value={f.first_name} onChange={set("first_name")} data-testid="hs-one-first" /></Field>
        <Field label="Last name" required><input className={drawerCtl} value={f.last_name} onChange={set("last_name")} data-testid="hs-one-last" /></Field>
        <Field label="Date of birth" required><input className={drawerCtl} value={f.dob} placeholder="MM/DD/YYYY" onChange={set("dob")} data-testid="hs-one-dob" /></Field>
        <Field label="State" required><input className={drawerCtl} value={f.state} placeholder="TX or Texas" onChange={set("state")} data-testid="hs-one-state" /></Field>
        <Field label="Mobile" required><input className={drawerCtl} value={f.phone} placeholder="(555) 123-4567" inputMode="tel" onChange={set("phone")} data-testid="hs-one-phone" /></Field>
        <Field label="Email"><input className={drawerCtl} value={f.email} placeholder="optional" onChange={set("email")} /></Field>
      </div>
      <Field label="Campaign" hint="Their texts and status show under this campaign. “Manual entries” collects everyone typed in by hand.">
        <select className={drawerCtl} value={campaign} onChange={(e) => pick(e.target.value)} data-testid="hs-one-campaign">
          <option value="">✍️ Manual entries</option>
          {camps.map((c) => <option key={c.id} value={c.id}>{c.name}{c.archived_at ? " (archived)" : ""}</option>)}
        </select>
      </Field>
      {!campaign && !manualCamp && (
        <Field label="HealthSherpa agent" hint="Whose HealthSherpa account sends the link (they close the sale).">
          <select className={drawerCtl} value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="">Me</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </Field>
      )}
      <TemplatePicker value={message} onPick={setMessage} />
      <Field label="First text" required hint="[Name] = their first name. Goes now if it's 8 am – 8 pm in their time zone, otherwise when their hours open.">
        <textarea className={`${drawerCtl} min-h-24`} value={message} maxLength={480} onChange={(e) => setMessage(e.target.value)} />
        <div className="mt-1"><TextLength text={message} /></div>
      </Field>
      <DrawerSection icon="💬" title="What they get">
        <Bubble who="First text" body={previewText(message, f.first_name.trim().split(" ")[0] || "Jane")} />
        <Bubble who="If they reply YES — follow-up" body={previewText(followup, f.first_name.trim().split(" ")[0] || "Jane")} />
        <p className="text-xs text-ink-muted">Then their answer queues them for HealthSherpa — auto mode sends the link. STOP or “no” = Do-Not-Call.</p>
      </DrawerSection>
    </Drawer>
  );
}

/** A status-panel chip's clients (No reply, Replied YES, HealthSherpa queue, Link
 * sent, Opted out…), newest first; click one for the full conversation. */
function StageListDrawer({ stage, onClose, onOpen, onError }: {
  stage: string; onClose: () => void; onOpen: (id: string) => void; onError: (m: string) => void;
}) {
  const [data, setData] = useState<ClientsResp | null>(null);
  const [q, setQ] = useState("");
  const [exporting, setExporting] = useState(false);
  const chip = CHIPS.find((c) => c.key === stage);
  useEffect(() => {
    const load = () => {
      const p = new URLSearchParams({ stage, limit: "300" });
      if (q.trim()) p.set("q", q.trim());
      api<ClientsResp>(`/hs-auth/campaigns/clients?${p}`).then(setData).catch((e) => onError(errText(e)));
    };
    const t0 = window.setTimeout(load, q ? 300 : 0);
    const t = window.setInterval(load, 5000);
    return () => { window.clearTimeout(t0); window.clearInterval(t); };
  }, [stage, q]); // eslint-disable-line react-hooks/exhaustive-deps
  const doExport = async () => {
    setExporting(true);
    try {
      const res = await authedFetch(`/hs-auth/campaigns/export?${new URLSearchParams({ stage })}`);
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url; a.download = `healthsherpa-${(CHIP_LABEL[stage] || stage).toLowerCase().replace(/\W+/g, "-")}.csv`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { onError(errText(e)); }
    finally { setExporting(false); }
  };
  const rows = data?.rows || [];
  return (
    <Drawer open title={`${CHIP_LABEL[stage] || stage}${data ? ` · ${data.total.toLocaleString()}` : ""}`} sub={chip?.hint} icon="📋" onClose={onClose}
            footer={<>
              <button className={btnGhost} disabled={exporting || !rows.length} onClick={doExport}>{exporting ? "Exporting…" : "⬇ Export this list"}</button>
              <button className={btn} onClick={onClose}>Close</button>
            </>}>
      <input className={`${inputCls} w-full`} placeholder="Search name, phone, company…" value={q} onChange={(e) => setQ(e.target.value)} />
      {!data ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>Nobody here right now.</Empty> : (
        <ul className="divide-y divide-hairline-soft" data-testid="hs-stage-list">
          {rows.map((r) => (
            <li key={r.id}>
              <button onClick={() => onOpen(r.id)} className="block w-full rounded-lg px-1 py-2 text-left hover:bg-black/[0.03]">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5 truncate text-sm font-bold text-ink">
                    {r.needs_reply && <span className="h-2 w-2 shrink-0 rounded-full bg-danger" title="Needs a reply" />}
                    {r.first_name} {r.last_name}
                  </span>
                  {stage === "YES" || stage === "HS_RUNNING" ? (
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[0.62rem] font-bold ${STAGE_CLS[r.stage] || STAGE_CLS.QUEUED}`}>{CHIP_LABEL[r.stage] || r.stage}</span>
                  ) : <span className="shrink-0 text-[0.66rem] text-ink-faint">{ago(r.last_text?.at || r.updated_at)}</span>}
                </div>
                <div className="text-[0.7rem] tabular-nums text-ink-muted">
                  {fmtPhone(r.phone)} · {r.state || "—"} · DOB {fmtDob(r.dob)}
                  {r.renewing_company && <> · <b className="text-ink-soft">{r.renewing_company}</b></>}
                  <span className="text-ink-faint"> · {r.campaign_name}</span>
                </div>
                {r.last_text && (
                  <div className="truncate text-[0.72rem] text-ink-soft">
                    <span className="font-semibold">{r.last_text.direction === "IN" ? "Client: " : "Us: "}</span>{r.last_text.body}
                  </div>
                )}
                {(stage === "HS_REVIEW" || stage === "HS_RUNNING" || stage === "LINK_SENT") && r.status_detail && (
                  <div className="truncate text-[0.7rem] text-pending">{r.status_detail}</div>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {data && data.total > rows.length && <div className="text-center text-xs text-ink-faint">Showing {rows.length} of {data.total} — Export for all.</div>}
    </Drawer>
  );
}

/** A small ⓘ that shows an explanation on hover (desktop) or tap (phone). */
function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <span ref={ref} className="relative ml-1 inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" aria-label="What does this do?" onClick={() => setOpen(!open)} data-testid="hs-info"
              className="flex h-4 w-4 items-center justify-center rounded-full border border-ink-faint text-[0.6rem] font-bold not-italic leading-none text-ink-muted hover:border-accent hover:text-accent">
        i
      </button>
      {open && (
        <span role="tooltip" className="absolute right-0 top-6 z-30 w-64 rounded-lg border border-hairline bg-white p-2.5 text-left text-[0.7rem] font-normal leading-snug text-ink-soft shadow-lg">
          {text}
        </span>
      )}
    </span>
  );
}

/** The follow-up after a YES — editable; the approved wording is the default. */
function FollowupField({ value, approved, onChange }: { value: string; approved: string; onChange: (v: string) => void }) {
  const custom = value.trim() !== approved.trim();
  return (
    <Field label="Follow-up after they reply YES" required
           hint="Sent the moment they reply positively. It must ask which company they're renewing — their answer is what queues HealthSherpa. [Name] = first name.">
      <textarea className={`${drawerCtl} min-h-20`} value={value} maxLength={480} onChange={(e) => onChange(e.target.value)}
                data-testid="hs-followup-text" />
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <TextLength text={value} />
        {custom ? (
          <>
            <span className="rounded-full bg-pending/15 px-2 py-0.5 text-[0.62rem] font-bold text-pending">Edited — not the approved wording</span>
            <button type="button" className="text-[0.7rem] font-semibold text-accent underline" onClick={() => onChange(approved)}>
              Reset to approved text
            </button>
          </>
        ) : <span className="rounded-full bg-success/15 px-2 py-0.5 text-[0.62rem] font-bold text-success">✓ Approved wording</span>}
      </div>
    </Field>
  );
}

type FileCheckResult = {
  ok: boolean; missing: string[]; rows: number; ready: number; fix_data: number; no_phone: number;
  duplicates: number; dnc: number; problem_count?: number;
  columns: { key: string; label: string; required: boolean; found: string | null }[];
  problems: { row: number; name: string; errors: string[] }[];
};

/** The file check, shown as soon as a file is chosen. */
function FileCheck({ r }: { r: FileCheckResult }) {
  const [showAll, setShowAll] = useState(false);
  const bad = r.missing.length > 0;
  const tone = bad || !r.ok ? "border-danger/30 bg-danger/5" : r.problems.length ? "border-pending/30 bg-pending/5" : "border-success/30 bg-success/5";
  const rows = showAll ? r.problems : r.problems.slice(0, 5);
  return (
    <div className={`space-y-2 rounded-lg border px-3 py-2.5 text-xs ${tone}`} data-testid="hs-file-check">
      <div className="font-bold text-ink">
        {bad ? "✗ Can't use this file — mandatory column missing"
          : !r.ok ? "✗ Nobody in this file can be texted"
          : r.problems.length ? `✓ File OK — ${r.ready} ready to text, some rows need attention` : `✓ File OK — all ${r.ready} clients ready to text`}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {r.columns.map((c) => (
          <span key={c.key} title={c.found ? `Found as “${c.found}”` : c.required ? "Missing — add this column" : "Optional"}
                className={`rounded-full px-2 py-0.5 font-semibold ${c.found ? "bg-success/15 text-success" : c.required ? "bg-danger text-white" : "bg-black/5 text-ink-faint"}`}>
            {c.found ? "✓" : c.required ? "✗" : "–"} {c.label}{!c.required && " (optional)"}
            {c.found && c.found.toLowerCase().replace(/[^a-z]/g, "") !== c.label.toLowerCase().replace(/[^a-z]/g, "") && <span className="font-normal opacity-75"> = “{c.found}”</span>}
          </span>
        ))}
      </div>
      {bad ? (
        <p className="text-danger">
          Missing: <b>{r.missing.join(", ")}</b>. The first row must be the headers: First Name, Last Name, Date of Birth, State, Phone
          (Download a template below).
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-ink-soft">
            <span>{r.rows} rows</span>
            <span className="text-success">✓ <b>{r.ready}</b> ready to text</span>
            {r.fix_data > 0 && <span className="text-danger"><b>{r.fix_data}</b> need fixing (not texted until fixed)</span>}
            {r.no_phone > 0 && <span className="text-danger"><b>{r.no_phone}</b> no valid mobile (skipped)</span>}
            {r.duplicates > 0 && <span><b>{r.duplicates}</b> duplicate numbers (skipped)</span>}
            {r.dnc > 0 && <span><b>{r.dnc}</b> on Do-Not-Call (skipped)</span>}
          </div>
          {r.problems.length > 0 && (
            <div className="rounded-md bg-white/70">
              <table className="w-full text-left text-[0.7rem]">
                <thead><tr className="text-ink-faint"><th className="px-2 py-1 font-semibold">Row</th><th className="px-2 py-1 font-semibold">Client</th><th className="px-2 py-1 font-semibold">Problem</th></tr></thead>
                <tbody className="divide-y divide-hairline-soft">
                  {rows.map((p) => (
                    <tr key={p.row}><td className="px-2 py-1 tabular-nums text-ink-faint">{p.row}</td><td className="px-2 py-1 font-semibold text-ink">{p.name}</td>
                      <td className="px-2 py-1 text-danger">{p.errors.join(" · ")}</td></tr>
                  ))}
                </tbody>
              </table>
              {(r.problem_count ?? r.problems.length) > 5 && (
                <button type="button" className="px-2 py-1 text-[0.7rem] font-semibold text-accent underline" onClick={() => setShowAll(!showAll)}>
                  {showAll ? "Show fewer" : `Show all ${r.problem_count ?? r.problems.length} rows with problems${(r.problem_count ?? 0) > r.problems.length ? ` (first ${r.problems.length})` : ""}`}
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Which of our numbers clients are texted from — picked automatically by the
 * client's state (the State column), so a Texas list goes out from Texas numbers. */
function SendingNumbers({ byState }: { byState: Record<string, number> }) {
  const states = Object.entries(byState || {});
  return (
    <div className="rounded-lg bg-black/5 px-3 py-2 text-xs text-ink-muted" data-testid="hs-state-numbers">
      <b className="text-ink">Sending number:</b>{" "}
      {states.length ? (
        <>each client is texted from one of our numbers <b>in their own state</b> when we have one —{" "}
          {states.map(([st, k]) => `${st} (${k})`).join(", ")}. Other states: any of our numbers. A client always
          hears from the same number.</>
      ) : (
        <>picked automatically from our numbers; a client always hears from the same number.</>
      )}
    </div>
  );
}

function RetextDrawer({ c, max, onClose, onSent }: {
  c: HsCampaign; max: number; onClose: () => void; onSent: (msg: string) => void;
}) {
  const n = c.counts.retext_ready ?? 0;
  const [body, setBody] = useState(c.template);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const preview = previewText(body);
  const send = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api<{ queued: number }>(`/hs-auth/campaigns/${c.id}/retext`, { method: "POST", body: JSON.stringify({ body }) });
      onSent(`Re-text started for ${r.queued} client${r.queued === 1 ? "" : "s"} who never replied in “${c.name}”.`);
    } catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  };
  return (
    <Drawer open title="Re-text no-replies" sub={c.name} icon="↻" onClose={onClose}
            footer={<>
              <button className={btnGhost} onClick={onClose}>Cancel</button>
              <button className={btn} disabled={busy || !body.trim()} onClick={send} data-testid="hs-retext-send">
                {busy ? "Starting…" : `Send to ${n} client${n === 1 ? "" : "s"}`}
              </button>
            </>}>
      {err && <div className="rounded-lg bg-danger/10 px-3 py-2 text-xs font-semibold text-danger">{err}</div>}
      <p className="text-xs text-ink-muted">
        Goes to the <b>{n}</b> client{n === 1 ? "" : "s"} in this campaign who <b>never replied</b>. Anyone who replied, opted out,
        or was texted in the last hour is left out. Like the first text, it goes out paced and only <b>8 am – 8 pm in
        their time zone</b>, from the <b>same number</b> they got the first text from. Pause or Stop on the campaign stops it too.
        Each client gets at most {max} re-texts in 30 days; after that the count resets, so nobody is wasted
        unless they opt out.
      </p>
      <TemplatePicker value={body} onPick={setBody} />
      <Field label="Message" required hint="[Name] or {name} = their first name. “Reply STOP to opt out.” is added if missing. Use approved wording — a different text than the first one often works better.">
        <textarea className={`${drawerCtl} min-h-24`} value={body} maxLength={480} onChange={(e) => setBody(e.target.value)} />
        <div className="mt-1"><TextLength text={body} /></div>
      </Field>
      <DrawerSection icon="💬" title="What they get">
        <Bubble who="Re-text" body={preview} />
        <p className="text-xs text-ink-muted">A reply to it works exactly like a reply to the first text.</p>
      </DrawerSection>
    </Drawer>
  );
}

function SettingsDrawer({ c, approvedFollowup, onClose, onSaved }: {
  c: HsCampaign; approvedFollowup: string; onClose: () => void; onSaved: () => void;
}) {
  const [f, setF] = useState({
    name: c.name, template: c.template, reminder_enabled: c.reminder_enabled, reminder_text: c.reminder_text || "",
    reminder_minutes: c.reminder_minutes, answer_wait_minutes: c.answer_wait_minutes, expire_minutes: c.expire_minutes,
    resend_on_expiry: c.resend_on_expiry, followup_text: c.followup_text || approvedFollowup,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const editable = c.status === "DRAFT" || c.status === "PAUSED";
  const save = async () => {
    setBusy(true); setErr(null);
    const body: Record<string, unknown> = { ...f };
    if (!editable) delete body.template;
    try { await api(`/hs-auth/campaigns/${c.id}`, { method: "PUT", body: JSON.stringify(body) }); onSaved(); }
    catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  };
  const num = (k: "reminder_minutes" | "answer_wait_minutes" | "expire_minutes") => (
    <input type="number" className={`${drawerCtl} w-24 tabular-nums`} value={f[k]}
           onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })} />
  );
  return (
    <Drawer open title="Campaign settings" sub={c.name} icon="⚙️" onClose={onClose}
            footer={<>
              <button className={btnGhost} onClick={onClose}>Cancel</button>
              <button className={btn} disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
            </>}>
      {err && <div className="rounded-lg bg-danger/10 px-3 py-2 text-xs font-semibold text-danger">{err}</div>}
      <Field label="Name"><input className={drawerCtl} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label="First text (the blast)" hint={editable ? `${f.template.length}/480` : "Pause the campaign to change it."}>
        <textarea className={`${drawerCtl} min-h-20`} disabled={!editable} value={f.template} maxLength={480}
                  onChange={(e) => setF({ ...f, template: e.target.value })} />
      </Field>
      <FollowupField value={f.followup_text} approved={approvedFollowup} onChange={(v) => setF({ ...f, followup_text: v })} />
      <p className="-mt-2 text-[0.7rem] text-ink-faint">Changes apply to follow-ups sent from now on — any time, no need to pause.</p>
      <Field label="Wait for their answer (minutes)" hint="After the follow-up. Then they show as “No answer yet” — a later answer still queues them.">
        {num("answer_wait_minutes")}
      </Field>
      <DrawerSection icon="🔗" title="After the HealthSherpa link is sent">
        <Field label="Link wait (minutes)" hint="Not authorized by then = “Check in HealthSherpa”, and auto mode moves to the next client. The link itself works 15 minutes.">{num("expire_minutes")}</Field>
        <label className="flex items-center gap-2 text-xs text-ink">
          <input type="checkbox" checked={f.resend_on_expiry} onChange={(e) => setF({ ...f, resend_on_expiry: e.target.checked })} />
          Resend the link once when it expires (HealthSherpa's “Resend by text”)
        </label>
        <label className="mt-3 flex items-center gap-2 text-xs font-semibold text-ink">
          <input type="checkbox" checked={f.reminder_enabled} onChange={(e) => setF({ ...f, reminder_enabled: e.target.checked })} />
          Send ONE reminder text if they haven't authorized yet
        </label>
        <Field label="Reminder at minute" plain>{num("reminder_minutes")}</Field>
        <Field label="Reminder text" hint="Use only approved 10DLC wording. The draft below is a placeholder.">
          <textarea className={`${drawerCtl} min-h-20`} value={f.reminder_text} maxLength={480}
                    onChange={(e) => setF({ ...f, reminder_text: e.target.value })} />
        </Field>
      </DrawerSection>
    </Drawer>
  );
}

function Bubble({ who, body, inbound, at, note }: { who: string; body: string | null; inbound?: boolean; at?: string | null; note?: string }) {
  return (
    <div className={`flex ${inbound ? "justify-start" : "justify-end"}`}>
      <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-xs ${inbound ? "bg-black/5 text-ink" : "bg-accent/10 text-ink"}`}>
        <div className="mb-0.5 text-[0.62rem] font-bold uppercase tracking-wide text-ink-faint">
          {who}{at ? ` · ${new Date(at).toLocaleString()}` : ""}
        </div>
        <div className="whitespace-pre-wrap">{body}</div>
        {note && <div className="mt-1 text-[0.62rem] font-semibold text-ink-faint">{note}</div>}
      </div>
    </div>
  );
}

/* Key HealthSherpa moments shown inline in the conversation (the full step
   list stays under "All steps"). */
const EVENT_LINE: Record<string, string> = {
  PENDING: "Queued for HealthSherpa", IN_PROGRESS: "HealthSherpa lookup started", LINK_SENT: "HealthSherpa texted them the link",
  AUTHORIZED: "Authorized ✓", UNCONFIRMED: "Not confirmed in time", EXPIRED: "Link expired — not authorized", NOT_FOUND: "Not found in HealthSherpa",
  NEEDS_REVIEW: "HealthSherpa needs a look", FAILED: "HealthSherpa stopped — problem", CANCELLED: "HealthSherpa stopped",
  INVALID: "HealthSherpa didn't accept the search",
};
const STEP_LINE: Record<string, string> = {
  no_answer: "No answer yet", opted_out: "Opted out (Do-Not-Call)", handled: "Marked as handled",
  question: "Asked a question — waiting for your reply", hesitant: "Hesitant — paused for your reply",
};
const DELIVERY: Record<string, string> = {
  QUEUED: "Sending…", SENDING: "Sending…", SENT: "Sent", DELIVERED: "Delivered", SKIPPED: "Not sent", CANCELLED: "Not sent",
};

function ClientDrawer({ c, stages, onClose, onChanged, onEdit, onRequeue, onStop }: {
  c: Client; stages: Record<string, string>; onClose: () => void; onChanged: (c: Client) => void;
  onEdit: () => void; onRequeue: () => void; onStop: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [allSteps, setAllSteps] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  type Item = { at: string; key: string; node: ReactNode };
  const items: Item[] = [];
  (c.texts || []).forEach((t) => {
    const at = t.at || "";
    const inbound = t.direction === "IN";
    const optOut = inbound && t.reply_kind && ["STOP", "NEGATIVE", "ABUSE", "WRONG"].includes(t.reply_kind);
    const who = inbound ? c.first_name : t.kind === "MANUAL" ? (t.sent_by_name || "You") : KIND_LABEL[t.kind] || "IAG";
    const foot = inbound ? (optOut ? "Opted out" : "") : t.status === "FAILED" ? `Failed${t.error ? ` — ${t.error}` : ""}` : DELIVERY[t.status] || "";
    items.push({ at, key: t.id, node: (
      <div className={`flex ${inbound ? "justify-start" : "justify-end"}`}>
        <div className={`max-w-[82%] rounded-2xl px-3 py-2 text-xs ${inbound ? "rounded-bl-md bg-black/[0.06] text-ink"
          : t.kind === "MANUAL" ? "rounded-br-md bg-accent text-white" : "rounded-br-md bg-accent/10 text-ink"}`}>
          <div className={`mb-0.5 text-[0.62rem] font-bold ${t.kind === "MANUAL" && !inbound ? "text-white/80" : "text-ink-faint"}`}>
            {who} · {at ? new Date(at).toLocaleString([], { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" }) : ""}
          </div>
          <div className="whitespace-pre-wrap leading-relaxed">{t.body}</div>
          {foot && <div className={`mt-0.5 text-right text-[0.6rem] ${t.status === "FAILED" || optOut ? "font-bold text-danger" : t.kind === "MANUAL" && !inbound ? "text-white/70" : "text-ink-faint"}`}>{foot}</div>}
        </div>
      </div>
    ) });
  });
  (c.events || []).forEach((ev, i) => {
    const line = (ev.status && EVENT_LINE[ev.status]) || (ev.step && STEP_LINE[ev.step]);
    if (!line) return;
    items.push({ at: ev.at, key: `e${i}`, node: (
      <div className="text-center text-[0.68rem] text-ink-faint">
        <span className="rounded-full bg-black/[0.04] px-2.5 py-1">
          {line}{ev.status && ev.detail && ev.status !== "PENDING" ? ` — ${ev.detail}` : ""} · {new Date(ev.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
        </span>
      </div>
    ) });
  });
  // Real time order: texts and steps carry different UTC offsets, so compare instants, not strings.
  items.sort((a, b) => (Date.parse(a.at) || 0) - (Date.parse(b.at) || 0));

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [items.length]);

  const send = async () => {
    const body = draft.trim();
    if (!body || sending) return;
    setSending(true); setErr(null);
    try {
      const d = await api<Client>(`/hs-auth/campaigns/clients/${c.id}/text`, { method: "POST", body: JSON.stringify({ body }) });
      setDraft(""); onChanged(d);
    } catch (e) { setErr(errText(e)); }
    finally { setSending(false); }
  };
  // Checked in HealthSherpa: they finished after auto mode moved on.
  const canMarkAuth = ["UNCONFIRMED", "EXPIRED"].includes(c.stage) || (c.stage === "HS_REVIEW" && c.status === "NEEDS_REVIEW");
  const markAuthorized = async () => {
    try { onChanged(await api<Client>(`/hs-auth/campaigns/clients/${c.id}/authorized`, { method: "POST" })); }
    catch (e) { setErr(errText(e)); }
  };
  const handled = async () => {
    try { onChanged(await api<Client>(`/hs-auth/campaigns/clients/${c.id}/handled`, { method: "POST" })); }
    catch (e) { setErr(errText(e)); }
  };

  const facts = [
    fmtPhone(c.phone), c.dob ? fmtDob(c.dob) : null, c.state, c.campaign_name,
  ].filter(Boolean).join(" · ");
  const action = c.stage === "INVALID" ? { label: "Fix data", run: onEdit }
    : (c.stage === "HS_REVIEW" || c.stage === "EXPIRED" || c.stage === "UNCONFIRMED") ? { label: "Queue for HealthSherpa again", run: onRequeue }
    : (c.stage === "HS_RUNNING" || c.stage === "LINK_SENT") ? { label: "Stop HealthSherpa", run: onStop } : null;

  return (
    <Drawer open title={`${c.first_name} ${c.last_name}`} icon="💬" onClose={onClose}
            sub={<>
              <span className={`mr-1.5 inline-block rounded-full px-2 py-0.5 text-[0.66rem] font-bold ${STAGE_CLS[c.stage] || ""}`}>{stages[c.stage] || c.stage}</span>
              <span className="tabular-nums">{facts}</span>
            </>}
            footer={
              <div className="w-full space-y-1.5" data-testid="hs-composer">
                {canMarkAuth && (
                  <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-pending/30 bg-pending/5 px-3 py-2 text-xs"
                       data-testid="hs-mark-auth">
                    <span className="text-ink-soft"><b className="text-pending">{c.stage === "UNCONFIRMED" ? "Not confirmed." : "Not authorized here."}</b> Check {c.first_name} in HealthSherpa — did they authorize?</span>
                    <button className={`${btn} px-3 py-1 text-[0.7rem]`} onClick={markAuthorized}>✓ Mark authorized</button>
                  </div>
                )}
                {err && <div className="rounded-lg bg-danger/10 px-3 py-1.5 text-xs font-semibold text-danger">{err}</div>}
                {c.can_text ? (
                  <>
                    <div className="flex items-end gap-2">
                      <textarea value={draft} rows={Math.min(5, Math.max(1, draft.split("\n").length))} maxLength={480}
                                placeholder={`Text ${c.first_name}…`} data-testid="hs-composer-input"
                                onChange={(e) => setDraft(e.target.value)}
                                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                                className="min-h-[38px] flex-1 resize-none rounded-2xl border border-hairline bg-white px-3 py-2 text-xs text-ink focus:border-accent focus:outline-none" />
                      <button className={`${btn} h-[38px] rounded-2xl px-4`} disabled={!draft.trim() || sending} onClick={send}>
                        {sending ? "…" : "Send"}
                      </button>
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-2 px-1 text-[0.66rem] text-ink-faint">
                      <span>
                        {c.texting_hours_ok === false
                          ? <b className="text-pending">⚠ Outside 8 am–8 pm for them ({c.state}) — send only if it can't wait.</b>
                          : <>From {c.from_number ? fmtPhone(c.from_number.replace(/^\+1/, "")) : "their usual number"} · Enter to send, Shift+Enter for a new line</>}
                      </span>
                      {draft.length > 0 && <span className="tabular-nums">{draft.length}/480</span>}
                    </div>
                  </>
                ) : (
                  <div className="rounded-xl bg-black/[0.04] px-3 py-2 text-center text-xs text-ink-muted">
                    {c.stage === "DNC" ? "Opted out — they can't be texted." : "Texting opens once the campaign has texted them."}
                  </div>
                )}
              </div>
            }>
      {c.needs_reply && (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-danger/30 bg-danger/5 px-3 py-2 text-xs">
          <span className="font-semibold text-danger">💬 {c.first_name} is waiting for a reply.</span>
          <button className="text-[0.7rem] font-semibold text-ink-muted underline hover:text-ink" onClick={handled}>No reply needed</button>
        </div>
      )}
      {(c.renewing_company || c.hs_app_id || action) && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl bg-black/[0.03] px-3 py-2 text-xs">
          {c.renewing_company && <span><span className="text-ink-faint">Renewing:</span> <b className="text-ink">{c.renewing_company}</b></span>}
          {c.hs_app_id && <span><span className="text-ink-faint">App ID:</span> <b className="tabular-nums text-ink">{c.hs_app_id}</b></span>}
          {action && <button className={`${btnGhost} ml-auto`} onClick={action.run}>{action.label}</button>}
        </div>
      )}
      <div className="space-y-2.5" data-testid="hs-texts">
        {items.length === 0 && <div className="py-6 text-center text-xs text-ink-muted">Not texted yet — the campaign texts them in order.</div>}
        {items.map((it) => <div key={it.key}>{it.node}</div>)}
        <div ref={endRef} />
      </div>
      <div className="border-t border-hairline pt-2">
        <button className="text-[0.7rem] font-semibold text-ink-faint hover:text-ink" onClick={() => setAllSteps((v) => !v)}>
          {allSteps ? "▾ Hide all steps" : "▸ All steps (for support)"}
        </button>
        {allSteps && (
          <div className="mt-2 space-y-1.5 text-xs">
            {[...(c.events || [])].reverse().map((ev, i) => (
              <div key={i} className="rounded-lg bg-black/5 px-3 py-1.5">
                <div className="flex justify-between gap-2">
                  <span className="font-bold text-ink">{ev.status || ev.step}</span>
                  <span className="tabular-nums text-ink-faint">{new Date(ev.at).toLocaleString()}</span>
                </div>
                {ev.detail && <div className={`mt-0.5 text-ink-muted ${ev.step === "diagnostic" ? "whitespace-pre-wrap break-words font-mono text-[0.62rem]" : ""}`}>{ev.detail}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    </Drawer>
  );
}

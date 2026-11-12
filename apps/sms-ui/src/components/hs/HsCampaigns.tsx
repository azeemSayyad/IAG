/* HealthSherpa text campaigns — the HealthSherpa page's "Text campaigns" tab.
 *
 * COMPLETELY separate from the Auto Dialer's Text campaigns: replies here are
 * never called. Upload clients (name, DOB, state, phone) + one message -> Start.
 * A positive reply gets the approved follow-up ("What company are you
 * renewing?"); their answer queues a HealthSherpa job that the agent's AUTO
 * MODE runs; STOP / no at any point = Do-Not-Call. Everything the closer needs
 * is in the table + Export. Backend: app/hs_auth/campaigns.py.
 */
import { useCallback, useEffect, useState } from "react";
import { api, apiUpload, authedFetch } from "../../lib/api";
import { stopJob } from "../../lib/hsBridge";
import { Drawer, DrawerSection, Field, drawerCtl } from "../Drawer";
import { Empty, ago, errText, fmtDob, fmtPhone } from "./ui";

export type Counts = {
  total: number; queued: number; sent: number; replied: number; positive: number; answered: number;
  no_answer: number; link_sent: number; authorized: number; expired: number; dnc: number; invalid: number; failed: number;
};
export type HsCampaign = {
  id: string; name: string; template: string; status: string; pause_reason: string | null;
  file_name: string | null; hs_agent_id: string; hs_agent_name: string | null;
  rows_in_file: number; skipped_invalid: number; skipped_no_phone: number; skipped_duplicate: number; skipped_dnc: number;
  answer_wait_minutes: number; reminder_enabled: boolean; reminder_text: string | null; reminder_minutes: number;
  expire_minutes: number; resend_on_expiry: boolean;
  created_at: string | null; started_at: string | null; finished_at: string | null;
  counts: Partial<Counts>; preview: string;
};
export type HsOverview = {
  campaigns: HsCampaign[]; can_manage: boolean; is_hs_agent: boolean; my_queue: number;
  usage: { used: number; limit: number; left: number };
  followup_text: string; reminder_draft: string; stages: Record<string, string>;
  sent_today: number; daily_cap: number; simulating: boolean;
};
type Text = {
  id: string; direction: "IN" | "OUT"; kind: string; body: string | null; status: string;
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
  last_text?: { direction: string; kind: string; body: string | null; at: string } | null;
  texts?: Text[]; events?: { at: string; step: string | null; status: string | null; detail: string }[];
};
type ClientsResp = { rows: Client[]; total: number; counts: Record<string, number> };
type Agent = { id: string; name: string; role: string };

const STAGE_CLS: Record<string, string> = {
  QUEUED: "bg-black/5 text-ink-muted", TEXTED: "bg-accent/10 text-accent", ASKED: "bg-pending/15 text-pending",
  NO_ANSWER: "bg-black/5 text-ink-soft", HS_QUEUED: "bg-accent/10 text-accent", HS_RUNNING: "bg-accent text-white",
  LINK_SENT: "bg-pending/15 text-pending", AUTHORIZED: "bg-success/15 text-success", EXPIRED: "bg-danger/10 text-danger",
  HS_REVIEW: "bg-pending/15 text-pending", DNC: "bg-danger/10 text-danger", NOT_SENT: "bg-black/5 text-ink-muted",
  INVALID: "bg-danger/10 text-danger",
};
const STAGE_ORDER = ["ALL", "QUEUED", "TEXTED", "ASKED", "NO_ANSWER", "HS_QUEUED", "HS_RUNNING", "LINK_SENT",
  "AUTHORIZED", "EXPIRED", "HS_REVIEW", "DNC", "NOT_SENT", "INVALID"];
const CAMP_PILL: Record<string, string> = {
  DRAFT: "bg-black/5 text-ink-muted", RUNNING: "bg-success/15 text-success", PAUSED: "bg-pending/15 text-pending",
  COMPLETED: "bg-accent/10 text-accent", STOPPED: "bg-black/5 text-ink-muted",
};
const CAMP_LABEL: Record<string, string> = {
  DRAFT: "Draft", RUNNING: "Sending", PAUSED: "Paused", COMPLETED: "All texted", STOPPED: "Stopped",
};
const KIND_LABEL: Record<string, string> = { BLAST: "Campaign text", FOLLOWUP: "Follow-up", REMINDER: "Reminder", REPLY: "Client" };

const btn = "rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white hover:bg-accent-hover disabled:opacity-50";
const btnGhost = "rounded-lg border border-hairline px-3 py-1.5 text-xs font-semibold text-ink-muted hover:bg-black/5 hover:text-ink disabled:opacity-50";
const inputCls = "rounded-lg border border-hairline bg-white px-2 py-1.5 text-xs text-ink";

const TEMPLATE_CSV = "First Name,Last Name,Date of Birth,State,Phone\nJane,Doe,01/31/1985,FL,(555) 123-4567\n";

export default function HsCampaigns({ overview, reloadOverview, agents, onEdit, onError, onNote }: {
  overview: HsOverview; reloadOverview: () => void; agents: Agent[];
  onEdit: (c: Client) => void; onError: (m: string) => void; onNote: (m: string) => void;
}) {
  const [campaignF, setCampaignF] = useState("");
  const [stage, setStage] = useState("ALL");
  const [q, setQ] = useState("");
  const [data, setData] = useState<ClientsResp | null>(null);
  const [creating, setCreating] = useState(false);
  const [settingsFor, setSettingsFor] = useState<HsCampaign | null>(null);
  const [detail, setDetail] = useState<Client | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const params = useCallback(() => {
    const p = new URLSearchParams();
    if (campaignF) p.set("campaign", campaignF);
    if (stage !== "ALL") p.set("stage", stage);
    if (q.trim()) p.set("q", q.trim());
    return p;
  }, [campaignF, stage, q]);

  const load = useCallback(() => {
    const p = params();
    p.set("limit", "300");
    return api<ClientsResp>(`/hs-auth/campaigns/clients?${p}`).then(setData).catch((e) => onError(errText(e)));
  }, [params, onError]);

  useEffect(() => { const t = window.setTimeout(load, q ? 300 : 0); return () => window.clearTimeout(t); }, [load, q]);
  useEffect(() => { const t = window.setInterval(load, 5000); return () => window.clearInterval(t); }, [load]);

  // Keep an open detail drawer live (new texts / status).
  useEffect(() => {
    if (!detail) return;
    const t = window.setInterval(() => {
      api<Client>(`/hs-auth/campaigns/clients/${detail.id}`).then(setDetail).catch(() => {});
    }, 5000);
    return () => window.clearInterval(t);
  }, [detail?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (c: HsCampaign, action: string) => {
    if (action === "stop" && !window.confirm(`Stop “${c.name}”? Nobody still waiting gets the text. Clients already talking to us carry on.`)) return;
    if (action === "delete" && !window.confirm(`Delete the draft “${c.name}”?`)) return;
    setBusy(c.id);
    try {
      if (action === "delete") await api(`/hs-auth/campaigns/${c.id}`, { method: "DELETE" });
      else await api(`/hs-auth/campaigns/${c.id}/${action}`, { method: "POST" });
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
      <section className="glass rounded-2xl p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-2xl">
            <h2 className="text-sm font-bold text-ink">HealthSherpa text campaigns</h2>
            <p className="text-xs text-ink-muted">
              Text a list of clients. A positive reply gets the approved follow-up asking which company they're renewing;
              their answer queues them for HealthSherpa, and <b>auto mode</b> sends them the authorization link.
              Replies here are <b>never called</b> — this is separate from the Auto Dialer's text campaigns.
              STOP or “no” at any point puts them on Do-Not-Call.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-black/5 px-3 py-1 text-xs font-bold tabular-nums text-ink-soft"
                  title="Every text from our numbers today (all campaigns) — carrier daily cap">
              Texts today: {overview.sent_today.toLocaleString()} / {overview.daily_cap.toLocaleString()}
            </span>
            {overview.can_manage && <button className={btn} onClick={() => setCreating(true)}>New campaign</button>}
          </div>
        </div>

        {overview.campaigns.length === 0 ? (
          <Empty>No HealthSherpa campaigns yet.{overview.can_manage ? " Press New campaign to upload a list." : ""}</Empty>
        ) : (
          <div className="mt-3 space-y-2">
            {overview.campaigns.map((c) => (
              <CampaignRow key={c.id} c={c} canManage={overview.can_manage} busy={busy === c.id}
                           onAction={(a) => act(c, a)} onSettings={() => setSettingsFor(c)}
                           onShow={() => { setCampaignF(c.id); setStage("ALL"); }} />
            ))}
          </div>
        )}
      </section>

      <section className="glass rounded-2xl p-4">
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
                      className={`rounded-full px-3 py-1 text-xs font-semibold ${stage === key ? "bg-accent text-white" : "bg-black/5 text-ink-muted hover:text-ink"}`}>
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

        {!data ? <Empty>Loading…</Empty> : data.rows.length === 0 ? (
          <Empty>{overview.campaigns.length ? "Nothing matches these filters." : "Clients show up here once a campaign is uploaded."}</Empty>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead>
                <tr className="border-b border-hairline text-[0.68rem] uppercase tracking-wide text-ink-faint">
                  <th className="py-2 pr-3 font-bold">Client</th>
                  <th className="py-2 pr-3 font-bold">DOB · State</th>
                  <th className="py-2 pr-3 font-bold">Last text</th>
                  <th className="py-2 pr-3 font-bold">Company renewing</th>
                  <th className="py-2 pr-3 font-bold">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline-soft">
                {data.rows.map((r) => (
                  <tr key={r.id} className="cursor-pointer align-top hover:bg-black/[0.02]" onClick={() => openDetail(r.id)}>
                    <td className="py-2.5 pr-3">
                      <div className="font-bold text-ink">{r.first_name} {r.last_name}</div>
                      <div className="tabular-nums text-ink-faint">{fmtPhone(r.phone)}</div>
                      {!campaignF && <div className="text-[0.66rem] text-ink-faint">{r.campaign_name}</div>}
                    </td>
                    <td className="py-2.5 pr-3 tabular-nums text-ink-soft">{fmtDob(r.dob)} · {r.state || "—"}</td>
                    <td className="max-w-[260px] py-2.5 pr-3">
                      {r.last_text ? (
                        <>
                          <div className="truncate text-ink-soft">
                            <span className={`font-semibold ${r.last_text.direction === "IN" ? "text-ink" : "text-ink-muted"}`}>
                              {r.last_text.direction === "IN" ? "Client: " : `${KIND_LABEL[r.last_text.kind] || "Us"}: `}
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
            {data.total > data.rows.length && (
              <div className="mt-3 text-center text-xs text-ink-faint">
                Showing {data.rows.length} of {data.total} — narrow it down, or Export for all of them.
              </div>
            )}
          </div>
        )}
      </section>

      {creating && <NewCampaignDrawer overview={overview} agents={agents} onClose={() => setCreating(false)}
                                      onCreated={(c, msg) => { setCreating(false); onNote(msg); reloadOverview(); setCampaignF(c.id); }} />}
      {settingsFor && <SettingsDrawer c={settingsFor} onClose={() => setSettingsFor(null)}
                                      onSaved={() => { setSettingsFor(null); reloadOverview(); }} />}
      {detail && <ClientDrawer c={detail} stages={stages} onClose={() => setDetail(null)}
                               onEdit={() => { onEdit(detail); setDetail(null); }}
                               onRequeue={() => requeue(detail)} onStop={() => stopRunning(detail)} />}
    </div>
  );
}

function StageDetail({ c }: { c: Client }) {
  const line = (() => {
    switch (c.stage) {
      case "AUTHORIZED": return c.authorized_at ? `Authorized ${new Date(c.authorized_at).toLocaleString()}` : null;
      case "HS_QUEUED": case "HS_RUNNING": case "LINK_SENT": case "EXPIRED": case "HS_REVIEW": return c.status_detail;
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

function CampaignRow({ c, canManage, busy, onAction, onSettings, onShow }: {
  c: HsCampaign; canManage: boolean; busy: boolean; onAction: (a: string) => void; onSettings: () => void; onShow: () => void;
}) {
  const n = c.counts || {};
  const stats: [string, number | undefined][] = [
    ["Texted", n.sent], ["Replied", n.replied], ["Positive", n.positive], ["Answered", n.answered],
    ["No answer", n.no_answer], ["Link sent", n.link_sent], ["Authorized", n.authorized], ["Expired", n.expired], ["DNC", n.dnc],
  ];
  return (
    <div className="rounded-xl border border-hairline p-3" data-testid="hs-campaign-row">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <button className="truncate text-sm font-bold text-ink hover:underline" onClick={onShow}>{c.name}</button>
            <span className={`rounded-full px-2 py-0.5 text-[0.66rem] font-bold ${CAMP_PILL[c.status] || CAMP_PILL.DRAFT}`}>
              {CAMP_LABEL[c.status] || c.status}
            </span>
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
            {c.status === "DRAFT" && <button className={btn} disabled={busy} onClick={() => onAction("start")}>Start</button>}
            {c.status === "RUNNING" && <button className={btnGhost} disabled={busy} onClick={() => onAction("pause")}>Pause</button>}
            {c.status === "PAUSED" && <button className={btn} disabled={busy} onClick={() => onAction("resume")}>Resume</button>}
            {(c.status === "RUNNING" || c.status === "PAUSED") && (
              <button className={btnGhost} disabled={busy} onClick={() => onAction("stop")}>Stop</button>
            )}
            <button className={btnGhost} onClick={onSettings}>Settings</button>
            {c.status === "DRAFT" && <button className={btnGhost} disabled={busy} onClick={() => onAction("delete")}>Delete</button>}
          </div>
        )}
      </div>
      {c.pause_reason && <div className="mt-2 rounded-lg bg-pending/10 px-3 py-1.5 text-xs font-semibold text-pending">{c.pause_reason}</div>}
      <div className="mt-2 grid grid-cols-3 gap-1.5 sm:grid-cols-5 lg:grid-cols-9">
        {stats.map(([label, v]) => (
          <div key={label} className="rounded-lg bg-black/5 px-2 py-1.5 text-center">
            <div className="text-sm font-extrabold tabular-nums text-ink">{(v ?? 0).toLocaleString()}</div>
            <div className="text-[0.62rem] uppercase tracking-wide text-ink-faint">{label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function NewCampaignDrawer({ overview, agents, onClose, onCreated }: {
  overview: HsOverview; agents: Agent[]; onClose: () => void;
  onCreated: (c: HsCampaign, msg: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [agent, setAgent] = useState("");
  const [template, setTemplate] = useState("Hi {name}, this is Insurance Alliance Group. Your 2027 health plan renewal is coming up — want help keeping your coverage? Reply YES.");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const preview = (() => {
    const t = template.replace(/\{\s*(first_?name|name)\s*\}/gi, "Jane").trim();
    return /stop/i.test(t) ? t : `${t} Reply STOP to opt out.`;
  })();

  const save = async () => {
    if (!file) { setErr("Choose the client file first."); return; }
    setBusy(true); setErr(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name || file.name.replace(/\.[^.]+$/, ""));
    fd.append("template", template);
    if (agent) fd.append("hs_agent_id", agent);
    try {
      const c = await apiUpload<HsCampaign>("/hs-auth/campaigns", fd);
      const skipped = [
        c.skipped_invalid && `${c.skipped_invalid} need fixing (Fix data — not texted until fixed)`,
        c.skipped_no_phone && `${c.skipped_no_phone} without a mobile number`,
        c.skipped_duplicate && `${c.skipped_duplicate} duplicate numbers`,
        c.skipped_dnc && `${c.skipped_dnc} on Do-Not-Call`,
      ].filter(Boolean);
      onCreated(c, `Campaign “${c.name}” ready with ${c.counts.total ?? 0} clients${skipped.length ? ` · ${skipped.join(" · ")}` : ""}. Press Start to send.`);
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
              <button className={btn} disabled={busy} onClick={save}>{busy ? "Uploading…" : "Create (starts as a draft)"}</button>
            </>}>
      {err && <div className="rounded-lg bg-danger/10 px-3 py-2 text-xs font-semibold text-danger">{err}</div>}
      <Field label="Client file" required
             hint="Excel or CSV with First Name, Last Name, Date of Birth, State and Phone. Names are cleaned, dates become MM/DD/YYYY.">
        <input type="file" accept=".xlsx,.csv" className={drawerCtl}
               onChange={(e) => setFile(e.target.files?.[0] || null)} />
      </Field>
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
      <Field label="First text (the blast)" required hint={`{name} = their first name. “Reply STOP to opt out.” is added if missing. ${template.length}/480`}>
        <textarea className={`${drawerCtl} min-h-24`} value={template} maxLength={480} onChange={(e) => setTemplate(e.target.value)} />
      </Field>
      <DrawerSection icon="💬" title="What they get">
        <Bubble who="Campaign text" body={preview} />
        <Bubble who="If they reply positively — approved follow-up (sent as is)" body={overview.followup_text} />
        <p className="text-xs text-ink-muted">
          Their answer (company name, “ok”, …) queues them for HealthSherpa. No answer in 10 minutes = “No answer yet”;
          a later answer still queues them. The 7-minute reminder is OFF until the wording is approved (Settings).
        </p>
      </DrawerSection>
    </Drawer>
  );
}

function SettingsDrawer({ c, onClose, onSaved }: { c: HsCampaign; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({
    name: c.name, template: c.template, reminder_enabled: c.reminder_enabled, reminder_text: c.reminder_text || "",
    reminder_minutes: c.reminder_minutes, answer_wait_minutes: c.answer_wait_minutes, expire_minutes: c.expire_minutes,
    resend_on_expiry: c.resend_on_expiry,
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
      <Field label="Wait for their answer (minutes)" hint="After the follow-up. Then they show as “No answer yet” — a later answer still queues them.">
        {num("answer_wait_minutes")}
      </Field>
      <DrawerSection icon="🔗" title="After the HealthSherpa link is sent">
        <Field label="Link wait (minutes)" hint="Not authorized by then = Expired, and auto mode moves on.">{num("expire_minutes")}</Field>
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

function ClientDrawer({ c, stages, onClose, onEdit, onRequeue, onStop }: {
  c: Client; stages: Record<string, string>; onClose: () => void; onEdit: () => void; onRequeue: () => void; onStop: () => void;
}) {
  const info: [string, string | null][] = [
    ["Phone", fmtPhone(c.phone)], ["Date of birth", fmtDob(c.dob)], ["State", c.state], ["Campaign", c.campaign_name],
    ["Their reply", c.yes_text], ["Company they're renewing", c.renewing_company],
    ["HealthSherpa", c.convo === "ANSWERED" || c.status === "AUTHORIZED" ? `${c.status_detail || ""}` : null],
    ["App ID", c.hs_app_id], ["Authorized", c.authorized_at ? new Date(c.authorized_at).toLocaleString() : null],
  ];
  return (
    <Drawer open title={`${c.first_name} ${c.last_name}`} icon="🧾" onClose={onClose}
            sub={<span className={`inline-block rounded-full px-2 py-0.5 text-[0.68rem] font-bold ${STAGE_CLS[c.stage] || ""}`}>{stages[c.stage] || c.stage}</span>}
            footer={<>
              {c.stage === "INVALID" && <button className={btn} onClick={onEdit}>Fix data</button>}
              {(c.stage === "HS_REVIEW" || c.stage === "EXPIRED") && <button className={btn} onClick={onRequeue}>Queue for HealthSherpa again</button>}
              {(c.stage === "HS_RUNNING" || c.stage === "LINK_SENT") && <button className={btnGhost} onClick={onStop}>Stop HealthSherpa</button>}
              <button className={btnGhost} onClick={onClose}>Close</button>
            </>}>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
        {info.filter(([, v]) => v).map(([k, v]) => (
          <div key={k} className={k === "HealthSherpa" || k === "Their reply" || k === "Company they're renewing" ? "col-span-2" : ""}>
            <dt className="text-[0.62rem] font-bold uppercase tracking-wide text-ink-faint">{k}</dt>
            <dd className="text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {c.convo_detail && <div className="rounded-lg bg-black/5 px-3 py-2 text-xs text-ink-muted">{c.convo_detail}</div>}
      <DrawerSection icon="💬" title="Texts">
        <div className="space-y-2" data-testid="hs-texts">
          {(c.texts || []).length === 0 && <div className="text-xs text-ink-muted">Not texted yet.</div>}
          {(c.texts || []).map((t) => (
            <Bubble key={t.id} who={KIND_LABEL[t.kind] || t.kind} body={t.body} inbound={t.direction === "IN"} at={t.at}
                    note={t.direction === "OUT"
                      ? ({ QUEUED: "Waiting to send", SENDING: "Sending", SENT: "Sent", DELIVERED: "Delivered", FAILED: `Failed${t.error ? ` — ${t.error}` : ""}`, SKIPPED: t.error || "Skipped", CANCELLED: "Not sent" } as Record<string, string>)[t.status]
                      : t.reply_kind && ["STOP", "NEGATIVE", "ABUSE", "WRONG"].includes(t.reply_kind) ? "Opted out → Do-Not-Call" : undefined} />
          ))}
        </div>
      </DrawerSection>
      <DrawerSection icon="🕘" title="History">
        <div className="space-y-1.5 text-xs">
          {[...(c.events || [])].reverse().map((ev, i) => (
            <div key={i} className="rounded-lg bg-black/5 px-3 py-1.5">
              <div className="flex justify-between gap-2">
                <span className="font-bold text-ink">{ev.status || ev.step}</span>
                <span className="tabular-nums text-ink-faint">{new Date(ev.at).toLocaleString()}</span>
              </div>
              {ev.detail && <div className="mt-0.5 text-ink-muted">{ev.detail}</div>}
            </div>
          ))}
        </div>
      </DrawerSection>
    </Drawer>
  );
}


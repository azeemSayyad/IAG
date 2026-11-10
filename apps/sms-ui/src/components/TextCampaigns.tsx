import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiUpload } from "../lib/api";
import { formatPhone } from "../lib/phone";
import { Drawer, DrawerSection, Field, drawerCtl } from "./Drawer";

/* Text campaigns — Auto Dialer page (super admin).
   Upload a CSV, write ONE message ({name} = first name), Start: the portal
   texts everyone through Telnyx in paced batches until the list is done or you
   pause. Replies come back to the dialer — positive ones are called.
   Backend: app/voice_dialer/campaigns.py. Separate from SMS Manager campaigns. */

type Counts = {
  total: number; queued: number; sent: number; delivered: number; failed: number;
  skipped: number; cancelled: number; replied: number; positive: number; opted_out: number;
};
type Campaign = {
  id: string; name: string; template: string; status: string; pause_reason: string | null;
  file_name: string | null; created_at: string | null; started_at: string | null; finished_at: string | null;
  skipped_at_upload: { rows: number; invalid: number; duplicate: number; dnc: number };
  counts: Counts;
};
type Overview = {
  campaigns: Campaign[];
  limits: { daily_cap: number; sent_today: number; numbers: string[]; gap_seconds: number;
    unknown_state_open: boolean; window: [number, number]; max_rows: number; max_template: number };
  business_name: string | null;
  stop_line: string;
  simulate: boolean;
};
type Recipient = {
  id: string; phone_number: string; first_name: string | null; state: string | null; status: string;
  from_number: string | null; error: string | null; sent_at: string | null; delivered_at: string | null;
  replied_at: string | null; reply_text: string | null; reply_kind: string | null;
};

const STATUS: Record<string, { label: string; tone: string }> = {
  DRAFT: { label: "Not started", tone: "bg-black/5 text-ink-muted" },
  RUNNING: { label: "Sending", tone: "bg-success/12 text-success" },
  PAUSED: { label: "Paused", tone: "bg-pending/12 text-pending" },
  COMPLETED: { label: "Done", tone: "bg-accent/10 text-accent" },
  STOPPED: { label: "Stopped", tone: "bg-black/5 text-ink-muted" },
};
const POSITIVE = ["YES", "REPLY"];
const SAMPLE = "Insurance Alliance Group: Hi {name}, 2027 Marketplace rates changed and you may qualify for a $0/mo plan. Reply YES for a quick call.";

const hour = (h: number) => (h === 12 ? "12 pm" : h === 0 || h === 24 ? "12 am" : h < 12 ? `${h} am` : `${h - 12} pm`);
const n = (v: number) => v.toLocaleString();
const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);
const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";

function errText(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  const m = msg.match(/\{.*\}$/s);
  if (m) {
    try {
      const d = JSON.parse(m[0])?.detail;
      if (typeof d === "string") return d;
    } catch { /* not JSON */ }
  }
  return msg;
}

/** What the client will actually get (mirrors campaigns.render on the server). */
function render(template: string, name: string, stopLine: string): string {
  const first = (name.trim().split(" ")[0] || "there").replace(/^\w/, (c) => c.toUpperCase());
  const text = template.replace(/\{\s*(first_?name|name)\s*\}/gi, first).trim();
  return /STOP/i.test(text) ? text : `${text} ${stopLine}`;
}

/** SMS segments: 160 chars (153 when split), or 70/67 once a non-GSM character (emoji, curly quote) is in. */
function segments(text: string): { chars: number; parts: number; unicode: boolean } {
  const unicode = /[^\n\r\x20-\x7E£¥èéùìòÇØøÅåÆæßÉÄÖÑÜ§¿äöñüà€]/.test(text);
  const one = unicode ? 70 : 160;
  const many = unicode ? 67 : 153;
  const chars = [...text].length;
  return { chars, parts: chars <= one ? 1 : Math.ceil(chars / many), unicode };
}

function Pill({ status }: { status: string }) {
  const s = STATUS[status] || { label: status, tone: "bg-black/5 text-ink-muted" };
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${s.tone}`}>{s.label}</span>;
}

function Progress({ c }: { c: Counts }) {
  const done = c.sent + c.failed + c.skipped; // texts actually handled — "not sent" (stopped) is not progress
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-black/8" title={`${n(done)} of ${n(c.total)} handled`}>
      <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct(done, c.total)}%` }} />
    </div>
  );
}

const btn = "rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-50";
const btnPrimary = `${btn} bg-accent text-white hover:bg-accent-hover`;
const btnGhost = `${btn} border border-hairline bg-white text-ink hover:bg-black/5`;

/* ================= The card on the page ================= */

export default function TextCampaigns() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setOv(await api<Overview>("/voice/dialer/campaigns"));
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, [load]);

  const act = async (c: Campaign, action: "start" | "pause") => {
    setBusy(c.id);
    try {
      await api(`/voice/dialer/campaigns/${c.id}/${action}`, { method: "POST" });
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
    }
  };

  if (!ov) {
    return (
      <section className="glass rounded-2xl p-5">
        <h2 className="text-base font-bold text-ink">Text campaigns</h2>
        <p className="mt-2 text-sm text-ink-faint">{error || "Loading…"}</p>
      </section>
    );
  }
  const L = ov.limits;
  const list = showAll ? ov.campaigns : ov.campaigns.slice(0, 4);
  const opened = ov.campaigns.find((c) => c.id === openId) || null;
  const capHit = L.sent_today >= L.daily_cap;

  return (
    <section className="glass rounded-2xl p-5" data-testid="text-campaigns">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-bold text-ink">
            Text campaigns {ov.simulate && <span className="ml-1 rounded-full bg-pending/12 px-2 py-0.5 text-[11px] font-semibold text-pending">Simulator · no real texts</span>}
          </h2>
          <p className="mt-0.5 text-xs text-ink-muted">
            <b className={capHit ? "text-danger" : "text-ink-soft"}>{n(L.sent_today)} / {n(L.daily_cap)}</b> texts today
            {" · "}{L.numbers.length} sending {L.numbers.length === 1 ? "number" : "numbers"}
            {" · "}{hour(L.window[0])}–{hour(L.window[1])} in each client's time
            {capHit && <span className="font-semibold text-danger"> · daily limit reached, sending resumes tomorrow</span>}
          </p>
        </div>
        <button onClick={() => setCreating(true)} className={btnPrimary}>+ New campaign</button>
      </div>

      {error && <div className="mt-3 rounded-lg bg-danger/10 px-3 py-2 text-xs font-medium text-danger">{error}</div>}

      {ov.campaigns.length === 0 ? (
        <div className="mt-4 rounded-xl border border-dashed border-hairline px-4 py-6 text-center text-sm text-ink-faint">
          No campaigns yet. Upload a CSV and write one message — replies that say yes are called automatically.
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-hairline-soft overflow-hidden rounded-xl border border-hairline-soft">
          {list.map((c) => {
            const k = c.counts;
            return (
              <li key={c.id} className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 bg-white/50 px-3 py-2.5 hover:bg-black/[0.03] md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1.3fr)_auto]"
                onClick={() => setOpenId(c.id)}>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-ink">{c.name}</span>
                    <Pill status={c.status} />
                  </div>
                  <div className="truncate text-xs text-ink-faint">
                    {c.pause_reason ? <span className="text-pending">{c.pause_reason}</span> : `${n(k.total)} people · ${when(c.started_at || c.created_at)}`}
                  </div>
                </div>
                <div className="order-last col-span-2 md:order-none md:col-span-1">
                  <Progress c={k} />
                  <div className="mt-1 text-[11px] text-ink-faint">{n(k.sent)} sent · {n(k.queued)} waiting{k.failed ? ` · ${n(k.failed)} failed` : ""}</div>
                </div>
                <div className="hidden text-xs text-ink-muted md:block">
                  <b className="text-ink">{n(k.replied)}</b> replied · <b className="text-success">{n(k.positive)}</b> to call · {n(k.opted_out)} opted out
                </div>
                <div className="flex justify-end" onClick={(e) => e.stopPropagation()}>
                  {(c.status === "DRAFT" || c.status === "PAUSED") && (
                    <button disabled={busy === c.id} onClick={() => act(c, "start")} className={btnPrimary}>
                      {c.status === "DRAFT" ? "Start" : "Resume"}
                    </button>
                  )}
                  {c.status === "RUNNING" && (
                    <button disabled={busy === c.id} onClick={() => act(c, "pause")} className={btnGhost}>Pause</button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {ov.campaigns.length > 4 && (
        <button onClick={() => setShowAll(!showAll)} className="mt-2 text-xs font-semibold text-accent hover:underline">
          {showAll ? "Show fewer" : `Show all ${ov.campaigns.length}`}
        </button>
      )}

      <NewCampaign open={creating} ov={ov} onClose={() => setCreating(false)}
        onCreated={async (id) => { setCreating(false); await load(); setOpenId(id); }} />
      {opened && <CampaignDrawer c={opened} ov={ov} onClose={() => setOpenId(null)} reload={load} />}
    </section>
  );
}

/* ================= Message editor (shared) ================= */

function MessageBox({ value, onChange, ov, disabled }: {
  value: string; onChange: (v: string) => void; ov: Overview; disabled?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const preview = render(value || "", "Maria", ov.stop_line);
  const seg = segments(preview);
  const biz = ov.business_name || "Insurance Alliance Group";
  const insertName = () => {
    const el = ref.current;
    if (!el) return onChange(`${value}{name}`);
    const [a, b] = [el.selectionStart, el.selectionEnd];
    onChange(value.slice(0, a) + "{name}" + value.slice(b));
    window.setTimeout(() => { el.focus(); el.setSelectionRange(a + 6, a + 6); }, 0);
  };
  return (
    <div className="space-y-2">
      <div className="relative">
        <textarea ref={ref} value={value} disabled={disabled} rows={5} maxLength={ov.limits.max_template}
          onChange={(e) => onChange(e.target.value)} className={`${drawerCtl} resize-y leading-relaxed`}
          placeholder={SAMPLE} />
        {!disabled && (
          <button type="button" onClick={insertName}
            className="absolute bottom-2 right-2 rounded-md border border-hairline bg-white px-2 py-0.5 text-[11px] font-semibold text-accent hover:bg-accent/8">
            + {"{name}"}
          </button>
        )}
      </div>
      <div className="rounded-xl bg-accent/8 px-3 py-2">
        <div className="mb-1 flex items-center justify-between text-[0.65rem] font-bold uppercase tracking-wide text-ink-faint">
          <span>Preview · Maria gets</span>
          <span className={seg.parts > 2 ? "text-pending" : ""}>{seg.chars} chars · {seg.parts} {seg.parts === 1 ? "text" : "texts"}{seg.unicode ? " (emoji/special chars)" : ""}</span>
        </div>
        <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink">{preview}</p>
      </div>
      <ul className="space-y-0.5 text-[11px] text-ink-faint">
        <li>{"{name}"} becomes each person's first name ("there" if the file has none).</li>
        <li>"{ov.stop_line}" is added when the message doesn't mention STOP.</li>
        {value && !value.toLowerCase().includes(biz.toLowerCase()) && (
          <li className="font-semibold text-pending">Start with "{biz}:" — carriers expect the business name, and texts without it get filtered.</li>
        )}
      </ul>
    </div>
  );
}

/* ================= New campaign ================= */

function NewCampaign({ open, ov, onClose, onCreated }: {
  open: boolean; ov: Overview; onClose: () => void; onCreated: (id: string) => void;
}) {
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [template, setTemplate] = useState(SAMPLE);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) { setName(""); setFile(null); setTemplate(SAMPLE); setError(null); }
  }, [open]);

  const save = async () => {
    if (!file) return setError("Choose the CSV file");
    setSaving(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("name", name.trim() || file.name.replace(/\.csv$/i, ""));
      fd.append("template", template);
      fd.append("file", file);
      const c = await apiUpload<Campaign>("/voice/dialer/campaigns", fd);
      onCreated(c.id);
    } catch (e) {
      setError(errText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer open={open} title="New text campaign" icon="💬" onClose={onClose}
      sub={<>Upload the list and write the message. Nothing is sent until you press <b>Start</b>.</>}
      footer={<>
        <button onClick={onClose} className={btnGhost}>Cancel</button>
        <button onClick={save} disabled={saving || !file || !template.trim()} className={btnPrimary}>
          {saving ? "Uploading…" : "Upload list"}
        </button>
      </>}>
      <DrawerSection icon="📄" title="The list" sub={`CSV with a phone column — first name and state are used if present. Up to ${n(ov.limits.max_rows)} rows.`}>
        <Field label="Campaign name">
          <input value={name} onChange={(e) => setName(e.target.value)} className={drawerCtl} placeholder={file ? file.name.replace(/\.csv$/i, "") : "e.g. Florida renewals — Nov"} />
        </Field>
        <Field label="CSV file" required plain>
          <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-hairline bg-black/[0.03] px-3.5 py-3 text-sm hover:border-accent">
            <span aria-hidden className="text-lg">⬆️</span>
            <span className="min-w-0 flex-1 truncate text-ink">{file ? file.name : "Choose a .csv file"}</span>
            {file && <span className="text-xs text-ink-faint">{(file.size / 1024).toFixed(0)} KB</span>}
            <input type="file" accept=".csv,text/csv" className="hidden" data-testid="campaign-file"
              onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </label>
        </Field>
        <p className="text-[11px] text-ink-faint">Invalid numbers, duplicates and anyone on Do-Not-Call are left out — you'll see the counts.</p>
      </DrawerSection>
      <DrawerSection icon="✍️" title="The message" sub="One message for everyone in this campaign." tone="accent2">
        <MessageBox value={template} onChange={setTemplate} ov={ov} />
      </DrawerSection>
      {error && <div className="rounded-lg bg-danger/10 px-3 py-2 text-xs font-medium text-danger">{error}</div>}
    </Drawer>
  );
}

/* ================= One campaign ================= */

const FILTERS: [string, string][] = [
  ["all", "All"], ["waiting", "Waiting"], ["sent", "Sent"], ["replied", "Replied"],
  ["positive", "To call"], ["opted_out", "Opted out"], ["failed", "Failed"],
];
const RSTATUS: Record<string, string> = {
  QUEUED: "Waiting", SENDING: "Sending", SENT: "Sent", DELIVERED: "Delivered",
  FAILED: "Failed", SKIPPED: "Skipped", CANCELLED: "Not sent",
};

function CampaignDrawer({ c, ov, onClose, reload }: {
  c: Campaign; ov: Overview; onClose: () => void; reload: () => Promise<void>;
}) {
  const [template, setTemplate] = useState(c.template);
  const [name, setName] = useState(c.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [flt, setFlt] = useState("all");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Recipient[]>([]);
  const [more, setMore] = useState(false);
  const editable = c.status === "DRAFT" || c.status === "PAUSED";
  const dirty = editable && (template !== c.template || name !== c.name);
  const k = c.counts;
  const up = c.skipped_at_upload;

  useEffect(() => { setTemplate(c.template); setName(c.name); }, [c.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchRows = useCallback(async (offset = 0) => {
    try {
      const r = await api<{ items: Recipient[]; has_more: boolean }>(
        `/voice/dialer/campaigns/${c.id}/recipients?filter=${flt}&offset=${offset}&limit=50${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`,
      );
      setRows((prev) => (offset ? [...prev, ...r.items] : r.items));
      setMore(r.has_more);
    } catch (e) {
      setError(errText(e));
    }
  }, [c.id, flt, q]);

  useEffect(() => {
    const t = window.setTimeout(() => fetchRows(0), q ? 300 : 0);
    return () => window.clearTimeout(t);
  }, [fetchRows, q]);
  // Live while sending: refresh the visible page with the card.
  useEffect(() => {
    if (c.status !== "RUNNING") return;
    const id = window.setInterval(() => fetchRows(0), 6000);
    return () => window.clearInterval(id);
  }, [c.status, fetchRows]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await reload();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };
  const save = () => run(() => api(`/voice/dialer/campaigns/${c.id}`, { method: "PUT", body: JSON.stringify({ name, template }) }));
  const action = (a: string) => run(() => api(`/voice/dialer/campaigns/${c.id}/${a}`, { method: "POST" }));
  const remove = () => run(async () => {
    await api(`/voice/dialer/campaigns/${c.id}`, { method: "DELETE" });
    onClose();
  });

  const stats: [string, number, string?][] = [
    ["People", k.total], ["Sent", k.sent], ["Delivered", k.delivered], ["Waiting", k.queued],
    ["Replied", k.replied, "text-ink"], ["To call", k.positive, "text-success"], ["Opted out", k.opted_out], ["Failed", k.failed + k.skipped, k.failed ? "text-danger" : undefined],
  ];

  return (
    <Drawer open title={c.name} icon="💬" onClose={onClose}
      sub={<><Pill status={c.status} /> <span className="ml-1">{c.file_name || ""}{c.started_at ? ` · started ${when(c.started_at)}` : ` · uploaded ${when(c.created_at)}`}</span></>}
      footer={<>
        {c.status === "DRAFT" && <button onClick={remove} disabled={busy} className={`${btnGhost} mr-auto text-danger`}>Delete</button>}
        {(c.status === "RUNNING" || c.status === "PAUSED") && (
          confirmStop ? (
            <span className="mr-auto flex items-center gap-1.5 text-xs text-ink-muted">
              Stop for good? {n(k.queued)} won't be texted.
              <button onClick={() => action("stop")} disabled={busy} className={`${btn} bg-danger text-white`}>Stop</button>
              <button onClick={() => setConfirmStop(false)} className={btnGhost}>Keep</button>
            </span>
          ) : <button onClick={() => setConfirmStop(true)} className={`${btnGhost} mr-auto`}>Stop campaign</button>
        )}
        {dirty && <button onClick={save} disabled={busy} className={btnGhost}>Save changes</button>}
        {(c.status === "DRAFT" || c.status === "PAUSED") && (
          <button onClick={() => (dirty ? save().then(() => action("start")) : action("start"))} disabled={busy} className={btnPrimary}>
            {c.status === "DRAFT" ? `Start sending to ${n(k.queued)}` : "Resume"}
          </button>
        )}
        {c.status === "RUNNING" && <button onClick={() => action("pause")} disabled={busy} className={btnPrimary}>Pause</button>}
      </>}>
      {c.pause_reason && <div className="rounded-lg bg-pending/12 px-3 py-2 text-xs font-medium text-pending">{c.pause_reason}</div>}
      {error && <div className="rounded-lg bg-danger/10 px-3 py-2 text-xs font-medium text-danger">{error}</div>}

      <div className="rounded-2xl border border-hairline bg-white p-3">
        <Progress c={k} />
        <div className="mt-3 grid grid-cols-4 gap-y-3">
          {stats.map(([label, v, tone]) => (
            <div key={label} className="text-center">
              <div className={`text-lg font-bold tabular-nums ${tone || "text-ink-soft"}`}>{n(v)}</div>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{label}</div>
            </div>
          ))}
        </div>
        {(up.invalid + up.duplicate + up.dnc > 0) && (
          <p className="mt-3 border-t border-hairline-soft pt-2 text-[11px] text-ink-faint">
            From {n(up.rows)} rows in the file, left out: {n(up.invalid)} invalid · {n(up.duplicate)} duplicate · {n(up.dnc)} on Do-Not-Call.
          </p>
        )}
      </div>

      <details className="group rounded-2xl border border-hairline bg-white p-3" open={c.status === "DRAFT"}>
        <summary className="cursor-pointer list-none text-sm font-bold text-ink">
          <span className="mr-1 inline-block transition-transform group-open:rotate-90">›</span> Message
          {c.status === "RUNNING" && <span className="ml-2 text-[11px] font-normal text-ink-faint">pause to edit</span>}
        </summary>
        <div className="mt-3 space-y-3">
          {editable && (
            <Field label="Campaign name"><input value={name} onChange={(e) => setName(e.target.value)} className={drawerCtl} /></Field>
          )}
          <MessageBox value={template} onChange={setTemplate} ov={ov} disabled={!editable} />
        </div>
      </details>

      <div className="rounded-2xl border border-hairline bg-white p-3">
        <div className="flex flex-wrap gap-1">
          {FILTERS.map(([key, label]) => (
            <button key={key} onClick={() => setFlt(key)}
              className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${flt === key ? "bg-accent text-white" : "bg-black/5 text-ink-muted hover:text-ink"}`}>
              {label}
            </button>
          ))}
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or number"
          className={`${drawerCtl} mt-2 py-1.5 text-xs`} />
        <ul className="mt-2 divide-y divide-hairline-soft">
          {rows.length === 0 && <li className="py-4 text-center text-xs text-ink-faint">Nobody here.</li>}
          {rows.map((r) => (
            <li key={r.id} className="py-2">
              <div className="flex items-center gap-2 text-xs">
                <span className="min-w-0 truncate font-semibold text-ink">{r.first_name || "—"}</span>
                <span className="tabular-nums text-ink-muted">{formatPhone(r.phone_number)}</span>
                {r.state && <span className="text-ink-faint">{r.state}</span>}
                <span className={`ml-auto rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                  r.reply_kind ? (POSITIVE.includes(r.reply_kind) ? "bg-success/12 text-success" : "bg-danger/10 text-danger")
                    : r.status === "FAILED" ? "bg-danger/10 text-danger" : "bg-black/5 text-ink-muted"}`}>
                  {r.reply_kind ? (POSITIVE.includes(r.reply_kind) ? "Replied · to call" : "Opted out") : RSTATUS[r.status] || r.status}
                </span>
              </div>
              {(r.reply_text || r.error) && (
                <div className={`mt-0.5 truncate text-[11px] ${r.error && !r.reply_text ? "text-danger" : "text-ink-soft"}`}>
                  {r.reply_text ? `“${r.reply_text}”` : r.error}
                </div>
              )}
            </li>
          ))}
        </ul>
        {more && <button onClick={() => fetchRows(rows.length)} className="mt-1 w-full text-center text-xs font-semibold text-accent hover:underline">Load more</button>}
      </div>
    </Drawer>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { getAccessToken } from "../lib/auth";

/*
 * LeadsTools — a native-React copy of the three Upload-Leads admin sections
 * (Pause-sending button, First-message editor, Campaign manager) so they can
 * sit ABOVE the SMS Manager. It calls the EXACT same backend endpoints the
 * portal's upload-leads.html calls via window.__ebAPI:
 *   __ebAPI.get('/ingestion/...')  ==  <origin>/api/v1/ingestion/...
 *   api('/ingestion/...')          ==  /api/v1/ingestion/...      (same URL)
 * Same Bearer access_token, same routes -> identical behaviour. No backend or
 * send-path change, so the first-template-only lockdown is untouched.
 *
 * Why native React and not an iframe of upload-leads.html: every section there
 * starts display:none and only becomes visible after its API call resolves, so
 * in a frame (where it didn't resolve) the whole thing rendered blank. This
 * component renders unconditionally and uses the SMS-UI's own working auth.
 *
 * Styling: uses the SMS Manager's own theme classes (glass cards, text-ink,
 * bg-success/danger/pending/accent) so it matches the surrounding UI and dark
 * mode — no hardcoded colours.
 */

// Multipart upload — the JSON api() wrapper forces Content-Type: application/json,
// which breaks FormData. Mirror __ebAPI.upload: same /api/v1 base + Bearer token,
// no JSON content-type (the browser sets the multipart boundary).
async function uploadForm<T = unknown>(path: string, fd: FormData): Promise<T> {
  const token = getAccessToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch("/api/v1" + path, { method: "POST", headers, body: fd });
  if (res.status === 401) { window.location.href = "/login.html"; throw new Error("Unauthorized"); }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

type Campaign = {
  id: string; name: string; send_state: string;
  total_leads: number; sent: number; delivered: number; remaining: number;
  yes: number; failed: number; drip_leads: number; drip_minutes: number;
  first_template?: string | null;   // the campaign's stored first message (null = backend default)
  provider?: string | null;          // backend provider key: "sinch" | "engage2"
};

// Provider chips per campaign. Sinch and Engage Cloud are wired to real, INDEPENDENT
// backend pipelines (own account/numbers); Telnyx/Twilio/Vonage are placeholders.
const PROVIDERS = ["Telnyx", "Twilio", "Sinch", "Engage Cloud", "Vonage"];
// The provider shown pre-selected/highlighted by default.
const DEFAULT_PROVIDER = "Sinch";
// Chip label <-> backend provider key (only these two are real backends).
const PROVIDER_KEY: Record<string, string> = { Sinch: "sinch", "Engage Cloud": "engage2" };
const KEY_LABEL: Record<string, string> = { sinch: "Sinch", engage2: "Engage Cloud" };

// Per-campaign first-message rules, enforced in the UI as you type and again on
// launch: a hard 160-char cap, NO emoji, and NO em/en-dashes. Keeps the body a
// plain, carrier-friendly single SMS. Only the message BODY is sanitised; the
// send path still tags it kind="first_template", so the first-template-only
// lockdown is untouched.
const CAMPAIGN_TMPL_MAX = 160;
// The default first message every new campaign is pre-filled with (editable per
// campaign before launch). Plain, carrier-friendly, under 160 chars.
const DEFAULT_FIRST_MSG = "Hey {first_name},it's Michael. Your health coverage might be flagged possible lapse. $0/mo before close. Reply YES, takes 2 min.";
function sanitizeTemplate(s: string): string {
  return (s || "")
    // any Unicode dash (hyphen .. horizontal-bar, U+2010-2015, plus minus U+2212) -> plain hyphen
    .replace(/[\u2010-\u2015\u2212]/g, "-")
    // strip emoji pictographs + regional-indicator flags + variation selector + keycap + ZWJ
    .replace(/[\p{Extended_Pictographic}\u{1F1E6}-\u{1F1FF}\uFE0F\u20E3\u200D]/gu, "")
    .slice(0, CAMPAIGN_TMPL_MAX);
}

// [label, badge-classes] per campaign send_state — themed tones (mirrors campBadge()).
const CAMP_BADGE: Record<string, [string, string]> = {
  running: ["Running", "bg-success/15 text-success"],
  ready:   ["Ready",   "bg-black/5 text-ink-muted"],
  paused:  ["Paused",  "bg-pending/15 text-pending"],
  stopped: ["Stopped", "bg-danger/15 text-danger"],
};

export default function LeadsTools() {
  // ---- toast (bottom-right, like the SMS Manager's kickNotice) ----
  const [toast, setToast] = useState<{ msg: string; tone: "accent" | "danger" } | null>(null);
  const toastTimer = useRef<number | null>(null);
  const showToast = useCallback((msg: string, tone: "accent" | "danger" = "accent") => {
    setToast({ msg, tone });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 3200);
  }, []);

  // ---- Pause / Resume (global send kill-switch) ----
  const [sendPaused, setSendPaused] = useState<boolean | null>(null);
  const loadSendState = useCallback(async () => {
    try { const r = await api<{ paused: boolean }>("/ingestion/sending/status"); setSendPaused(!!r?.paused); }
    catch { /* leave unchanged if not permitted */ }
  }, []);
  const toggleSend = useCallback(async () => {
    const paused = sendPaused === true;
    if (!paused && !window.confirm("PAUSE all outbound sending?\n\nThis fully stops the first template, AI replies, follow-ups, reminders and the pacing engine for every lead until you press Resume.")) return;
    try {
      const r = await api<{ paused: boolean }>("/ingestion/sending/" + (paused ? "resume" : "stop"), { method: "POST", body: "{}" });
      setSendPaused(!!r?.paused);
      showToast(r?.paused ? "All sending PAUSED — nothing will send" : "Sending resumed", r?.paused ? "danger" : "accent");
    } catch { showToast("Could not change sending state", "danger"); }
  }, [sendPaused, showToast]);

  // ---- New-campaign autofill ----
  // Every new campaign's message box is pre-filled with DEFAULT_FIRST_MSG and is
  // freely editable before launch. (The backend keeps its own send-path fallback.)

  // ---- Automation engine on/off (capacity pacing + fatigue guard) ----
  // Live toggle of the two runtime engine flags via the manager endpoint (Redis
  // override; no redeploy). The first-template lockdown is NEVER exposed here.
  const [engine, setEngine] = useState<{ capacity: boolean; fatigue: boolean; capSrc?: string; fatSrc?: string; ceiling?: number } | null>(null);
  const loadEngine = useCallback(async () => {
    try {
      const r = await api<{ flags?: { capacity_pacing_enabled: boolean; fatigue_enabled: boolean; capacity_pacing_source?: string; fatigue_source?: string }; capacity?: { release_ceiling?: number } }>("/sms/manager/engine-status");
      if (r?.flags) setEngine({ capacity: !!r.flags.capacity_pacing_enabled, fatigue: !!r.flags.fatigue_enabled, capSrc: r.flags.capacity_pacing_source, fatSrc: r.flags.fatigue_source, ceiling: r.capacity?.release_ceiling });
    } catch { /* hidden if not permitted (non-manager) */ }
  }, []);
  const toggleEngine = useCallback(async (name: "CAPACITY_PACING_ENABLED" | "FATIGUE_ENABLED", enabled: boolean) => {
    try {
      await api<{ enabled: boolean }>("/sms/manager/engine-flag", { method: "POST", body: JSON.stringify({ name, enabled }) });
      showToast((name === "FATIGUE_ENABLED" ? "Fatigue guard " : "Capacity pacing ") + (enabled ? "ON" : "OFF"), enabled ? "accent" : "danger");
      await loadEngine();
    } catch { showToast("Could not change engine flag (permission or network)", "danger"); }
  }, [loadEngine, showToast]);

  // ---- Campaign manager (up to 5 CSV campaigns) ----
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [rate, setRate] = useState<Record<string, { leads: number; minutes: number }>>({});
  const [uploading, setUploading] = useState(false);
  // New-campaign draft (load list -> enter message -> pick provider -> launch).
  const [draft, setDraft] = useState<{ file: File | null; fileName: string; message: string; provider: string } | null>(null);
  // Per-campaign message + provider (UI-side; sent in the upload for future backend wiring).
  const [meta, setMeta] = useState<Record<string, { message: string; provider: string }>>({});
  // Per-campaign collapse state (UI only) — click the header to collapse/expand.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  // Per-campaign inline rename: id -> draft name while editing (undefined = not editing).
  const [renameVal, setRenameVal] = useState<Record<string, string | undefined>>({});
  const loadCampaigns = useCallback(async () => {
    try {
      const r = await api<{ campaigns: Campaign[] }>("/ingestion/campaigns?_=" + Date.now()); // cache-bust
      const list = r?.campaigns || [];
      setCampaigns(list);
      // Seed editable drip values once per campaign — don't clobber in-progress edits on the 15s refresh.
      setRate(prev => { const next = { ...prev }; for (const c of list) if (!(c.id in next)) next[c.id] = { leads: c.drip_leads, minutes: c.drip_minutes }; return next; });
    } catch { /* hidden if not permitted */ }
  }, []);
  const openDraft = useCallback(() => {
    if (campaigns.length >= 5) { showToast("Up to 5 campaigns — stop/remove one first", "danger"); return; }
    // Pre-fill with the standard default message (editable before launch).
    setDraft({ file: null, fileName: "", message: sanitizeTemplate(DEFAULT_FIRST_MSG), provider: DEFAULT_PROVIDER });
  }, [campaigns.length, showToast]);
  const pickDraftFile = useCallback(() => {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = ".csv,text/csv";
    inp.addEventListener("change", (e) => {
      const f = (e.target as HTMLInputElement).files?.[0]; if (!f) return;
      setDraft(d => (d ? { ...d, file: f, fileName: f.name } : d));
    });
    inp.click();
  }, []);
  const launchDraft = useCallback(async () => {
    if (!draft?.file) { showToast("Load a lead list (CSV) first", "danger"); return; }
    const cleanMsg = sanitizeTemplate(draft.message).trim();
    if (!cleanMsg) { showToast("Enter the first message for this campaign", "danger"); return; }
    const fd = new FormData();
    fd.append("file", draft.file); fd.append("name", draft.file.name);
    fd.append("first_template", cleanMsg);   // per-campaign message: 160 cap, no emoji/em-dash
    fd.append("provider", draft.provider);
    setUploading(true);
    try {
      const resp = await uploadForm<{ campaign?: { id?: string } }>("/ingestion/campaigns/upload", fd);
      const id = resp?.campaign?.id;
      if (id) setMeta(m => ({ ...m, [id]: { message: cleanMsg, provider: draft.provider } }));
      showToast("Campaign created: " + draft.file.name, "accent");
      setDraft(null); await loadCampaigns();
    } catch { showToast("Upload failed", "danger"); }
    finally { setUploading(false); }
  }, [draft, loadCampaigns, showToast]);
  const campAction = useCallback(async (id: string, act: string) => {
    try {
      if (act === "rate") {
        const e = rate[id];
        const r = await api<{ message?: string }>("/ingestion/campaigns/" + id + "/drip", { method: "POST", body: JSON.stringify({ leads: e?.leads, minutes: e?.minutes }) });
        showToast(r?.message || "Rate saved", "accent");
      } else if (act === "remove") {
        if (!window.confirm("Remove this campaign and its leads?")) return;
        await api("/ingestion/campaigns/" + id, { method: "DELETE" });
        showToast("Campaign removed", "accent"); await loadCampaigns();
      } else {
        const r = await api<{ message?: string }>("/ingestion/campaigns/" + id + "/" + act, { method: "POST", body: "{}" });
        showToast(r?.message || "Done", "accent"); await loadCampaigns();
      }
    } catch { showToast("Could not " + act + " — another campaign may be running", "danger"); await loadCampaigns(); }
  }, [rate, loadCampaigns, showToast]);

  // ---- Rename a campaign (admin) — display name only; never touches leads/sending ----
  const cancelRename = useCallback((id: string) => {
    setRenameVal(p => { const n = { ...p }; delete n[id]; return n; });
  }, []);
  const saveRename = useCallback(async (id: string) => {
    const name = (renameVal[id] || "").trim();
    if (!name) { showToast("Enter a campaign name", "danger"); return; }
    try {
      const res = await api<{ message?: string }>("/ingestion/campaigns/" + id + "/rename", { method: "POST", body: JSON.stringify({ name }) });
      showToast(res?.message || "Renamed", "accent");
      setRenameVal(p => { const n = { ...p }; delete n[id]; return n; });
      await loadCampaigns();
    } catch { showToast("Could not rename (admins only)", "danger"); }
  }, [renameVal, loadCampaigns, showToast]);

  // ---- Set which provider a campaign sends through ("sinch" | "engage2"). Only
  // changes the account/numbers this campaign uses — never the send-path lockdown. ----
  const setCampaignProvider = useCallback(async (id: string, label: string) => {
    const key = PROVIDER_KEY[label];
    if (!key) { showToast(label + " is not available", "danger"); return; }
    try {
      const res = await api<{ message?: string }>("/ingestion/campaigns/" + id + "/provider", { method: "POST", body: JSON.stringify({ provider: key }) });
      showToast(res?.message || ("Provider set to " + label), "accent");
      await loadCampaigns();
    } catch { showToast("Could not set provider (admins only)", "danger"); }
  }, [loadCampaigns, showToast]);

  useEffect(() => {
    loadSendState(); loadCampaigns(); loadEngine();
    const id = window.setInterval(loadCampaigns, 15000); // keep campaign progress/state fresh
    return () => window.clearInterval(id);
  }, [loadSendState, loadCampaigns, loadEngine]);

  // ---- shared theme classes (match the SMS Manager glass/ink theme) ----
  const inputCls = "h-9 rounded-lg border border-hairline bg-white px-2 text-center text-sm text-ink";
  // Fixed (non-inverting) tones so buttons read correctly in dark mode too —
  // `ink` is avoided because it flips to a light shade.
  const BTN_TONE: Record<string, string> = {
    success: "bg-success", danger: "bg-danger", pending: "bg-pending", primary: "bg-accent hover:bg-accent-hover",
  };
  const btnCls = (tone: keyof typeof BTN_TONE) =>
    `inline-flex h-9 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-4 text-sm font-semibold text-white disabled:opacity-50 ${BTN_TONE[tone]}`;
  const outlineBtn = "inline-flex h-9 items-center rounded-lg border border-hairline px-4 text-sm font-semibold text-ink-muted hover:text-ink";

  return (
    <div className="space-y-4">
      {toast && (
        <div className={`fixed bottom-6 right-6 z-[60] rounded-lg px-4 py-2 text-sm font-semibold text-white shadow-lg ${toast.tone === "danger" ? "bg-danger" : "bg-ink"}`}>{toast.msg}</div>
      )}

      {/* Global send-paused banner */}
      {sendPaused && (
        <div role="alert" className="rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm font-semibold text-danger">
          All outbound sending is PAUSED — the first template, AI replies, follow-ups, reminders and the pacing engine are all stopped. Nothing will send to any lead until you press Resume.
        </div>
      )}

      {/* Automation engine on/off — shows only when the manager engine endpoint is reachable */}
      {engine && (
        <section className="glass rounded-2xl p-5">
          <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-base font-semibold text-ink">Automation engine</div>
              <p className="mt-1 text-[0.8125rem] text-ink-muted">Capacity-sized pacing &amp; fatigue guard. Toggles live (no redeploy). The first-template lockdown is never affected.</p>
            </div>
            {typeof engine.ceiling === "number" && <span className="rounded-full bg-black/5 px-2.5 py-0.5 text-xs font-semibold text-ink-muted">release ceiling {engine.ceiling}/tick</span>}
          </div>
          <div className="mt-3 flex flex-col gap-2.5">
            {([
              ["Capacity pacing", "CAPACITY_PACING_ENABLED", engine.capacity, engine.capSrc, "Releases only as many leads as free licensed agents can absorb, per state."],
              ["Fatigue guard", "FATIGUE_ENABLED", engine.fatigue, engine.fatSrc, "Skips a number texted too recently (per-phone frequency cap + cooldown)."],
            ] as [string, "CAPACITY_PACING_ENABLED" | "FATIGUE_ENABLED", boolean, string | undefined, string][]).map(([label, flag, on, src, desc]) => (
              <div key={flag} className="flex items-center justify-between gap-3 rounded-xl border border-hairline-soft bg-black/5 px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm font-semibold text-ink">{label}
                    <span className={`rounded-full px-2 py-0.5 text-[0.62rem] font-bold uppercase ${on ? "bg-success/15 text-success" : "bg-black/10 text-ink-faint"}`}>{on ? "On" : "Off"}</span>
                    {src === "override" && <span className="text-[0.62rem] text-ink-faint">manual override</span>}
                  </div>
                  <div className="mt-0.5 text-xs text-ink-muted">{desc}</div>
                </div>
                <button type="button" role="switch" aria-checked={on} onClick={() => toggleEngine(flag, !on)}
                  className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-success" : "bg-black/25"}`}>
                  <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${on ? "translate-x-[22px]" : "translate-x-0.5"}`} />
                </button>
              </div>
            ))}
          </div>
          <p className="mt-2.5 text-xs text-ink-faint">The master switch <code>SAME_DAY_PACING_ENABLED</code> must also be on (server env) for pacing to release — these two control the engine on top of it.</p>
        </section>
      )}

      {/* Campaigns */}
      <section className="glass rounded-2xl p-5">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-base font-semibold text-ink">Campaigns</div>
            <p className="mt-1 text-[0.8125rem] text-ink-muted">Upload up to 5 CSVs — each runs independently. Only one campaign sends at a time.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {/* Pause/Resume — moved here beside New campaign (same toggleSend backend, unchanged). */}
            <button type="button" onClick={toggleSend} className={`inline-flex h-9 items-center gap-2 rounded-lg px-4 text-sm font-semibold text-white ${sendPaused ? "bg-success" : "bg-danger"}`}>
              <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
              {sendPaused ? "Resume sending" : "Pause sending"}
            </button>
            <button type="button" onClick={openDraft} disabled={uploading} className="inline-flex h-9 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-65">+ New campaign</button>
          </div>
        </div>

        {/* New-campaign draft: load lead list -> enter message -> pick provider -> launch */}
        {draft && (
          <div className="mt-3.5 space-y-3 rounded-xl border border-accent/30 bg-accent/5 p-4">
            <div className="text-sm font-bold text-ink">New campaign</div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={pickDraftFile} className={outlineBtn}>{draft.fileName || "Load lead list (CSV)"}</button>
              {draft.fileName && <span className="text-xs font-semibold text-success">✓ {draft.fileName}</span>}
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold text-ink-muted">First message for this campaign</div>
              <textarea rows={3} maxLength={CAMPAIGN_TMPL_MAX} value={draft.message} onChange={e => setDraft(d => (d ? { ...d, message: sanitizeTemplate(e.target.value) } : d))} placeholder="Enter the first text this campaign sends…" className="w-full resize-y rounded-xl border border-hairline bg-white px-3 py-2.5 text-sm text-ink" />
              <div className="mt-1 text-xs text-ink-faint">Use <code>{"{first_name}"}</code> for the lead's name. No emoji or dashes. {draft.message.length}/{CAMPAIGN_TMPL_MAX}</div>
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold text-ink-muted">Provider</div>
              <div className="flex flex-wrap gap-1.5">
                {PROVIDERS.map(pn => (
                  <button key={pn} type="button"
                    onClick={() => PROVIDER_KEY[pn] ? setDraft(d => (d ? { ...d, provider: pn } : d)) : showToast(pn + " is not available", "danger")}
                    className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${draft.provider === pn ? "border-accent bg-accent/10 text-accent" : "border-hairline text-ink-muted hover:text-ink"}`}>{pn}</button>
                ))}
              </div>
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={launchDraft} disabled={uploading} className={btnCls("success")}>{uploading ? "Launching…" : "Launch campaign"}</button>
              <button type="button" onClick={() => setDraft(null)} className={outlineBtn}>Cancel</button>
            </div>
          </div>
        )}

        <div className="mt-3.5 flex flex-col gap-3">
          {campaigns.length === 0 && <div className="text-sm text-ink-muted">No campaigns yet — upload a CSV to create one.</div>}
          {campaigns.map(c => {
            const st = c.send_state;
            const badge = CAMP_BADGE[st] || CAMP_BADGE.ready;
            const r = rate[c.id] || { leads: c.drip_leads, minutes: c.drip_minutes };
            return (
              <div key={c.id} className="flex flex-col gap-3.5 rounded-xl border border-hairline-soft bg-black/5 px-4 py-4">
                <div className="flex w-full items-center justify-between gap-3">
                  {renameVal[c.id] !== undefined ? (
                    /* Inline rename (admin) — display name only; sending is untouched. */
                    <div className="flex w-full min-w-0 items-center gap-2">
                      <input autoFocus type="text" maxLength={255} value={renameVal[c.id] || ""}
                        onChange={e => setRenameVal(p => ({ ...p, [c.id]: e.target.value }))}
                        onKeyDown={e => { if (e.key === "Enter") saveRename(c.id); else if (e.key === "Escape") cancelRename(c.id); }}
                        placeholder="Campaign name"
                        className="h-8 min-w-0 flex-1 rounded-lg border border-accent/50 bg-white px-2.5 text-sm font-semibold text-ink" />
                      <button type="button" onClick={() => saveRename(c.id)} className="inline-flex h-8 shrink-0 items-center rounded-lg bg-accent px-3 text-xs font-semibold text-white hover:bg-accent-hover">Save</button>
                      <button type="button" onClick={() => cancelRename(c.id)} className="inline-flex h-8 shrink-0 items-center rounded-lg border border-hairline px-2.5 text-xs font-semibold text-ink-muted hover:text-ink">Cancel</button>
                    </div>
                  ) : (<>
                    <button type="button" onClick={() => setCollapsed(p => ({ ...p, [c.id]: !p[c.id] }))}
                      aria-expanded={!collapsed[c.id]} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className={`shrink-0 text-ink-faint transition-transform ${collapsed[c.id] ? "" : "rotate-90"}`}><path d="M9 6l6 6-6 6" /></svg>
                      <span className="break-words text-base font-bold text-ink">{c.name || "Campaign"}</span>
                    </button>
                    <span className="flex shrink-0 items-center gap-2">
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${badge[1]}`}>{badge[0]}</span>
                      {/* Rename (admin): inline-edit the campaign's display name. */}
                      <button type="button" onClick={() => setRenameVal(p => ({ ...p, [c.id]: c.name || "" }))} title="Rename campaign"
                        className="inline-flex h-7 items-center rounded-lg border border-hairline px-2.5 text-xs font-semibold text-ink-muted hover:text-ink">Rename</button>
                      {/* Remove beside the status badge — same delete endpoint, unchanged. */}
                      <button type="button" onClick={() => campAction(c.id, "remove")} title="Remove campaign"
                        className="inline-flex h-7 items-center rounded-lg border border-danger/30 px-2.5 text-xs font-semibold text-danger hover:bg-danger/10">Remove</button>
                    </span>
                  </>)}
                </div>
                {!collapsed[c.id] && (<>
                <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-y border-hairline-soft py-2.5">
                  {([["Leads", c.total_leads, "text-ink"], ["Sent", c.sent, "text-ink"], ["Delivered", c.delivered, "text-success"], ["Left", c.remaining, "text-ink"], ["Total yes", c.yes, "text-success"], ["Failed text", c.failed, "text-danger"]] as [string, number, string][]).map(([lab, val, col]) => (
                    <div key={lab} className="flex min-w-[58px] flex-col gap-0.5">
                      <span className="text-[0.64rem] font-bold uppercase tracking-wide text-ink-faint">{lab}</span>
                      <span className={`text-lg font-bold tabular-nums ${col}`}>{val || 0}</span>
                    </div>
                  ))}
                </div>
                {/* per-campaign message + provider (Sinch / Engage Cloud select which account sends) */}
                <div className="space-y-2">
                  <div className="rounded-lg border border-hairline-soft bg-white/40 px-3 py-2 text-xs text-ink-muted">
                    <span className="font-semibold text-ink-faint">Message: </span>{c.first_template || meta[c.id]?.message || "Uses the default first message"}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {(() => { const curProvider = KEY_LABEL[c.provider || "sinch"] || DEFAULT_PROVIDER; return PROVIDERS.map(pn => {
                      const sel = curProvider === pn;
                      return (
                        <button key={pn} type="button"
                          onClick={() => setCampaignProvider(c.id, pn)}
                          className={`rounded-lg border px-2.5 py-1 text-[0.7rem] font-semibold ${sel ? "border-accent bg-accent/10 text-accent" : "border-hairline text-ink-faint hover:text-ink"}`}>{pn}</button>
                      );
                    }); })()}
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-1.5 text-[0.82rem] text-ink-muted">
                    send
                    <input type="number" min={1} max={5000} value={r.leads} onChange={e => setRate(p => ({ ...p, [c.id]: { ...r, leads: parseInt(e.target.value, 10) || 0 } }))} className={`${inputCls} w-16`} />
                    every
                    <input type="number" min={1} max={1440} value={r.minutes} onChange={e => setRate(p => ({ ...p, [c.id]: { ...r, minutes: parseInt(e.target.value, 10) || 0 } }))} className={`${inputCls} w-14`} />
                    min
                    <button type="button" onClick={() => campAction(c.id, "rate")} className={btnCls("primary")}>Save</button>
                  </div>
                  <div className="flex gap-1.5">
                    {/* Single start/stop toggle: running -> Stop; otherwise Start (resume if
                        paused, else run). Hits the same backend endpoints — unchanged. */}
                    {st === "running"
                      ? <button type="button" onClick={() => campAction(c.id, "stop")} className={btnCls("danger")}>Stop</button>
                      : <button type="button" onClick={() => campAction(c.id, st === "paused" ? "resume" : "run")} className={btnCls("success")}>Start</button>}
                  </div>
                </div>
                </>)}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

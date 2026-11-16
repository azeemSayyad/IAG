/* AUTOMATION STATUS — a slim bar at the top of the HealthSherpa text campaigns tab:
 *   1 one health line: Working / Needs attention / Blocked · auto mode · texting hours
 *   2 alerts ONLY when a person has to act (login, auto mode off with clients
 *     waiting, stuck, carrier pause, limits, needs reply) — each with one button
 *   3 chips (No reply, Replied YES, HealthSherpa queue, Working now, Link sent,
 *     Opted out…) — each opens a drawer with those clients
 *   4 today as a funnel  5 the client being worked on right now  6 details ▾
 * plus a POPUP (sound + desktop notification) when HealthSherpa needs a login.
 * Server: GET /hs-auth/campaigns/status (app/hs_auth/campaigns.automation_status).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { ago } from "./ui";

export type HsIssue = {
  level: "error" | "warn" | "info"; code: string; title: string; detail: string;
  request_id?: string; since?: string | null; opens_at?: string | null; agent?: string; campaign_id?: string;
};
type Running = {
  id: string; name: string; status: string; step: string | null; detail: string | null;
  started_at: string | null; link_sent_at: string | null; last_report_at: string | null; login: boolean;
};
type AgentStatus = {
  id: string; name: string; queue: number; alive: boolean; auto_on: boolean; seen_at: string | null;
  ext_version: string | null; tab_hidden: boolean | null; reason: string | null;
  usage: { used: number; limit: number; left: number }; running: Running | null;
};
export type HsStatus = {
  now: string; level: "ok" | "warn" | "error"; issues: HsIssue[];
  pipeline: Record<string, number>;
  today: { texts_sent: number; daily_cap: number; replies: number; answered: number; links_sent: number;
           authorized: number; stopped: number; opted_out: number };
  hours: { start_hour: number; end_hour: number; ignored: boolean; waiting_open: number; waiting_closed: number;
           paused: number; eastern_open: boolean; pacific_open: boolean;
           states: { state: string; waiting: number; open: boolean; opens_at: string | null }[] };
  agents: AgentStatus[];
  recent_problems: { id: string; name: string; status: string; detail: string | null; step: string | null; at: string | null }[];
  campaigns: { running: number; paused: number };
};

const POLL_MS = 5000;

export function useHsStatus(enabled: boolean) {
  const [st, setSt] = useState<HsStatus | null>(null);
  const [at, setAt] = useState<number>(0);
  const load = useCallback(() => {
    api<HsStatus>("/hs-auth/campaigns/status").then((s) => { setSt(s); setAt(Date.now()); }).catch(() => {});
  }, []);
  useEffect(() => {
    if (!enabled) return;
    load();
    const t = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(t);
  }, [enabled, load]);
  return { st, at, reload: load };
}

// ------------------------------------------------------------------ helpers

const STEP: Record<string, string> = {
  start: "Starting", open: "Opening HealthSherpa", login: "WAITING FOR HEALTHSHERPA LOGIN",
  search_page: "Opening Search Marketplace", fill: "Typing client details", search: "Searching the Marketplace",
  results: "Reading results", add: "Adding the 2027 application", popup: "Authorization window",
  consent: "Ticking consent", link: "Link sent — waiting for the client", refresh: "Checking status",
  resend: "Resending the link", finish: "Finishing", done: "Done",
};
const PROBLEM: Record<string, string> = {
  NOT_FOUND: "Not found in HealthSherpa", NEEDS_REVIEW: "Needs a look", FAILED: "Failed",
  CANCELLED: "Stopped", INVALID: "HealthSherpa didn't accept the search",
};

function clock(iso: string | null | undefined, tz?: string) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", ...(tz ? { timeZone: tz } : {}) });
}
function dayClock(iso: string | null | undefined) {
  if (!iso) return "";
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? clock(iso) : `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${clock(iso)}`;
}
function mmss(fromIso: string | null, nowMs: number) {
  if (!fromIso) return "";
  const s = Math.max(0, Math.round((nowMs - new Date(fromIso).getTime()) / 1000));
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}:${String(s % 60).padStart(2, "0")}`;
}
function hourLabel(h: number) { return `${h % 12 || 12} ${h < 12 ? "am" : "pm"}`; }

const LEVEL = {
  error: { dot: "bg-danger", box: "border-danger/30 bg-danger/5", text: "text-danger", icon: "⛔" },
  warn: { dot: "bg-pending", box: "border-pending/30 bg-pending/5", text: "text-pending", icon: "⚠️" },
  info: { dot: "bg-accent", box: "border-accent/20 bg-accent/5", text: "text-accent", icon: "ℹ️" },
  ok: { dot: "bg-success", box: "border-success/30 bg-success/5", text: "text-success", icon: "✅" },
  idle: { dot: "bg-ink-faint", box: "border-hairline bg-black/5", text: "text-ink-muted", icon: "⏸" },
} as const;

// ------------------------------------------------------------------ panel

/* The chips — each opens a drawer with those clients (onList). */
export const CHIPS: { key: string; label: string; hint: string; tone?: "danger" | "warn" | "success" }[] = [
  { key: "QUEUED", label: "Not texted yet", hint: "Waiting for the first text (texting hours / daily cap / pause)" },
  { key: "TEXTED", label: "No reply", hint: "Got the campaign text, never replied" },
  { key: "YES", label: "Replied YES", hint: "Everyone who replied positively, wherever they are now" },
  { key: "ASKED", label: "Asked company", hint: "Said yes — waiting for which company they're renewing" },
  { key: "NEEDS_REPLY", label: "Needs reply", hint: "A person should answer them", tone: "danger" },
  { key: "HS_QUEUED", label: "HealthSherpa queue", hint: "Answered — waiting for auto mode to run them" },
  { key: "HS_RUNNING", label: "Working now", hint: "The extension is on this client in HealthSherpa" },
  { key: "LINK_SENT", label: "Link sent", hint: "HealthSherpa link sent — waiting for the client (up to 10 min, then the next client)" },
  { key: "AUTHORIZED", label: "Authorized", hint: "Done ✔", tone: "success" },
  { key: "EMAIL_CHECK", label: "Confirming email", hint: "HealthSherpa has a different mobile — we texted them to confirm the email it has (10 min); YES = the link goes by email" },
  { key: "UNCONFIRMED", label: "Check in HealthSherpa", hint: "Not authorized within 10 min — auto mode moved on. The link works until 15 min: check them in HealthSherpa and press Mark authorized", tone: "warn" },
  { key: "EXPIRED", label: "Expired", hint: "The 15-minute link ran out" },
  { key: "HS_REVIEW", label: "Needs a look", hint: "The HealthSherpa run stopped — open a client for the reason", tone: "warn" },
  { key: "DNC", label: "Opted out", hint: "STOP / no / wrong number — never texted again" },
];
export const CHIP_LABEL: Record<string, string> = Object.fromEntries(CHIPS.map((c) => [c.key, c.label]));

// Only these become alerts — each is something a person has to DO.
const ACTION_CODES = new Set(["LOGIN", "AUTO_OFF", "STUCK", "PAUSED", "CAP", "LIMIT", "NO_NUMBERS", "NEEDS_REPLY"]);

export function AutomationPanel({ st, at, onList, onOpen }: {
  st: HsStatus; at: number; onList: (stage: string) => void; onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem("hsStatusDetails") === "1"; } catch { return false; } });
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => { const t = window.setInterval(() => setNowMs(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  const toggle = () => { const v = !open; setOpen(v); try { localStorage.setItem("hsStatusDetails", v ? "1" : "0"); } catch { /* */ } };

  const alerts = st.issues.filter((i) => ACTION_CODES.has(i.code) && !(i.code === "PAUSED" && i.level !== "error"));
  const runningNow = st.agents.map((a) => a.running).filter(Boolean) as Running[];
  const open_ = (st.pipeline.ASKED || 0) + (st.pipeline.HS_QUEUED || 0) + (st.pipeline.LINK_SENT || 0) + (st.pipeline.NO_ANSWER || 0);
  // Say what is REALLY going on — "Working" only when something is actually moving.
  const [level, headline, what]: [keyof typeof LEVEL, string, string] =
    alerts.some((i) => i.level === "error") ? ["error", "Blocked", ""]
    : alerts.length ? ["warn", "Needs attention", ""]
    : runningNow.length ? ["ok", "Working", `HealthSherpa on ${runningNow.map((r) => r.name).join(", ")}`]
    : st.campaigns.running > 0 ? ["ok", "Working", `sending texts (${st.campaigns.running} campaign${st.campaigns.running === 1 ? "" : "s"})`]
    : ["idle", "No campaigns running", open_ > 0 ? `${open_} conversation${open_ === 1 ? "" : "s"} still open` : ""];
  const h = st.hours;
  const hours = h.ignored ? "Texting hours ignored (test)"
    : h.eastern_open && h.pacific_open ? `Texting hours open until ${hourLabel(h.end_hour)} client time`
    : h.eastern_open ? `Texting hours open (Pacific from ${hourLabel(h.start_hour)} PT)`
    : h.pacific_open ? `Texting hours closed in the East — open out West`
    : `Outside texting hours — first texts wait until ${hourLabel(h.start_hour)} client time`;
  const hoursTone = h.ignored || (h.eastern_open && h.pacific_open) ? "text-ink-muted" : "text-pending";
  const running = runningNow;
  const t = st.today;
  const pct = t.links_sent ? Math.round((100 * t.authorized) / t.links_sent) : null;

  const chipClick = (key: string) => {
    if (key === "HS_RUNNING" && running.length === 1) { onOpen(running[0].id); return; }  // the lead being worked on
    onList(key);
  };

  return (
    <section className="rounded-2xl border border-hairline bg-white/70 p-3" data-testid="hs-automation">
      {/* 1 — health in one line */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span className="relative flex h-2.5 w-2.5 shrink-0">
            {(level === "error" || level === "warn") && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${LEVEL[level].dot}`} />}
            <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${LEVEL[level].dot}`} />
          </span>
          <b className={`text-sm ${LEVEL[level].text}`} data-testid="hs-automation-level">{headline}</b>
          {what && <span className="text-ink-soft">· {what}</span>}
          {st.agents.map((a) => (
            <span key={a.id} className="text-ink-soft">
              · Auto mode <b className={a.alive ? "text-success" : "text-danger"}>{a.alive ? "ON" : "OFF"}</b> ({a.name}
              {a.seen_at ? `, seen ${ago(a.seen_at)}` : ""})
            </span>
          ))}
          <span className={hoursTone}>· 🕗 {hours}</span>
        </div>
        <button onClick={toggle} className="rounded-lg px-2 py-0.5 text-[0.7rem] font-semibold text-ink-muted hover:bg-black/5">
          {open ? "Hide details ▴" : "Details ▾"}
        </button>
      </div>

      {/* 2 — alerts, only when someone has to act */}
      {alerts.length > 0 && (
        <ul className="mt-2 space-y-1.5" data-testid="hs-issues">
          {alerts.map((i, n) => (
            <li key={n} className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-1.5 ${LEVEL[i.level].box}`}>
              <div className="min-w-0 flex-1 text-xs">
                <b className={LEVEL[i.level].text}>{i.code === "LOGIN" ? "🔐" : LEVEL[i.level].icon} {i.title}</b>
                {i.since && <span className="text-ink-faint"> · since {dayClock(i.since)}</span>}
                {i.detail && <div className="text-ink-soft">{i.detail}</div>}
              </div>
              {i.request_id && <button onClick={() => onOpen(i.request_id!)} className={chipBtn}>Open client</button>}
              {i.code === "NEEDS_REPLY" && <button onClick={() => onList("NEEDS_REPLY")} className={chipBtn}>Show them</button>}
              {i.code === "AUTO_OFF" && <button onClick={() => onList("HS_QUEUED")} className={chipBtn}>Who's waiting</button>}
            </li>
          ))}
        </ul>
      )}

      {/* 3 — clickable cards: each opens the list */}
      <div className="mt-2.5 grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(92px, 1fr))" }} data-testid="hs-pipeline">
        {CHIPS.map((c) => {
          const n = st.pipeline[c.key] || 0;
          if (c.key === "QUEUED" && !n) return null;
          const bar = !n ? "transparent"
            : c.tone === "danger" ? "var(--color-danger, #c0392b)"
            : c.tone === "warn" ? "var(--color-pending, #b7791f)"
            : c.tone === "success" ? "var(--color-success, #2f855a)"
            : "var(--color-accent, #23408e)";
          const num = !n ? "text-ink-faint"
            : c.tone === "danger" ? "text-danger" : c.tone === "warn" ? "text-pending"
            : c.tone === "success" ? "text-success" : "text-ink";
          const bg = n && c.tone === "danger" ? "bg-danger/5" : n && c.tone === "warn" ? "bg-pending/5" : "bg-white/70";
          return (
            <button key={c.key} title={c.hint} onClick={() => chipClick(c.key)} data-testid={`hs-chip-${c.key}`}
                    className={`group relative overflow-hidden rounded-xl border border-hairline ${bg} px-2 py-2 text-center transition hover:-translate-y-px hover:border-accent/40 hover:shadow-md`}>
              <span className="absolute inset-y-0 left-0 w-1" style={{ background: bar }} />
              {c.key === "HS_RUNNING" && n > 0
                ? <span className="absolute right-1.5 top-1.5 h-2 w-2 animate-pulse rounded-full bg-accent" />
                : <span className="absolute right-1.5 top-0.5 text-xs text-ink-faint opacity-0 transition group-hover:opacity-100">›</span>}
              <div className={`text-xl font-extrabold tabular-nums leading-none ${num}`}>{n.toLocaleString()}</div>
              <div className={`mt-1 text-[0.68rem] font-semibold leading-tight ${n ? "text-ink-muted" : "text-ink-faint"}`}>{c.label}</div>
            </button>
          );
        })}
      </div>

      {/* 4 — today as a funnel */}
      <div className="mt-2 text-[0.72rem] text-ink-soft" data-testid="hs-today">
        <b className="text-ink">Today:</b> {t.texts_sent} texts sent → {t.replies} replied → {t.answered} answered company →{" "}
        {t.links_sent} links sent → <b className="text-success">{t.authorized} authorized</b>
        {pct !== null && <span className="text-ink-faint"> ({pct}% of links)</span>}
        {t.opted_out > 0 && <span className="text-ink-faint"> · {t.opted_out} opted out</span>}
      </div>

      {/* 5 — what's happening right now */}
      {running.map((r) => (
        <button key={r.id} onClick={() => onOpen(r.id)} data-testid="hs-now"
                className={`mt-2 flex w-full flex-wrap items-center gap-x-2 rounded-lg px-3 py-1.5 text-left text-xs ${r.login ? "bg-danger/10 text-danger" : "bg-accent/10 text-accent"}`}>
          <b>▶ Now: {r.name}</b>
          <span>{STEP[r.step || "start"] || r.step}</span>
          <span className="tabular-nums opacity-80">
            {r.status === "LINK_SENT" ? `link sent ${mmss(r.link_sent_at, nowMs)} ago (15:00 limit)` : `running ${mmss(r.started_at, nowMs)}`}
          </span>
          <span className="ml-auto font-semibold underline">Open</span>
        </button>
      ))}

      {/* 6 — details: rarely needed */}
      {open && (
        <div className="mt-3 grid gap-3 border-t border-hairline pt-3 text-xs lg:grid-cols-3" data-testid="hs-details">
          <div>
            <div className={detailHead}>Auto mode</div>
            {st.agents.length === 0 ? <div className="text-ink-faint">No campaign yet.</div> : st.agents.map((a) => (
              <div key={a.id} className="text-ink-soft">
                <b className="text-ink">{a.name}</b> — {a.alive ? "on" : a.auto_on ? "not seen recently" : "off"}
                {a.seen_at && <>, page seen {ago(a.seen_at)}</>}{a.ext_version && <>, extension v{a.ext_version}</>}
                {a.alive && a.tab_hidden && <>, tab in background</>}. Queue {a.queue} · HealthSherpa runs today {a.usage.used}/{a.usage.limit}.
              </div>
            ))}
            <div className="mt-1 text-ink-faint">
              Now {clock(st.now, "America/New_York")} ET · {clock(st.now, "America/Los_Angeles")} PT · updated {Math.max(0, Math.round((nowMs - at) / 1000))}s ago
            </div>
          </div>
          <div>
            <div className={detailHead}>First texts waiting · {hourLabel(h.start_hour)}–{hourLabel(h.end_hour)} client time</div>
            {h.states.length === 0 && !h.paused ? <div className="text-ink-faint">Nothing waiting.</div> : (
              <div className="flex flex-wrap gap-1">
                {h.states.map((x) => (
                  <span key={x.state} className={`rounded-full px-2 py-0.5 font-semibold ${x.open ? "bg-success/15 text-success" : "bg-black/5 text-ink-muted"}`}>
                    {x.state} {x.waiting} · {x.open ? "sending" : x.opens_at ? `opens ${dayClock(x.opens_at)}` : "closed"}
                  </span>
                ))}
                {h.paused > 0 && <span className="rounded-full bg-pending/15 px-2 py-0.5 font-semibold text-pending">{h.paused} in paused campaigns</span>}
              </div>
            )}
            <div className="mt-1 text-ink-faint">Replies, follow-ups and links go any hour.</div>
          </div>
          <div>
            <div className={detailHead}>Recent problems (24 h)</div>
            {st.recent_problems.length === 0 ? <div className="text-ink-faint">None.</div> : st.recent_problems.slice(0, 5).map((p) => (
              <button key={p.id} onClick={() => onOpen(p.id)} className="block w-full rounded px-1 py-0.5 text-left hover:bg-black/5">
                <b className="text-ink">{p.name}</b> <span className="text-danger">{PROBLEM[p.status] || p.status}</span>
                <span className="text-ink-faint"> · {dayClock(p.at)}</span>
                {p.detail && <div className="truncate text-ink-muted">{p.detail}</div>}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

const chipBtn = "rounded-lg border border-hairline bg-white px-2 py-1 text-[0.7rem] font-semibold text-ink-muted hover:text-ink";
const detailHead = "mb-1 text-[0.66rem] font-bold uppercase tracking-wide text-ink-faint";

// ------------------------------------------------------------------ login popup

function beep() {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    [0, 0.35].forEach((t) => {
      const o = ctx.createOscillator(); const g = ctx.createGain();
      o.frequency.value = 880; g.gain.value = 0.15;
      o.connect(g); g.connect(ctx.destination);
      o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.2);
    });
    window.setTimeout(() => ctx.close(), 1200);
  } catch { /* no audio */ }
}

/** A popup (with a sound, a flashing tab title and a desktop notification when
 *  allowed) the moment HealthSherpa needs a login. Shown again for each new wait. */
export function LoginAlert({ st, meIsAgent }: { st: HsStatus | null; meIsAgent: boolean }) {
  const issue = st?.issues.find((i) => i.code === "LOGIN") || null;
  const key = issue ? `${issue.request_id}|${issue.since}` : "";
  const [dismissed, setDismissed] = useState<string>("");
  const announced = useRef("");
  const [perm, setPerm] = useState<string>(() => (typeof Notification !== "undefined" ? Notification.permission : "denied"));

  useEffect(() => {
    if (!issue || announced.current === key) return;
    announced.current = key;
    beep();
    try {
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        new Notification("HealthSherpa login needed", { body: issue.detail, tag: "hs-login", requireInteraction: true });
      }
    } catch { /* not allowed */ }
  }, [issue, key]);

  // Flash the tab title while it waits.
  useEffect(() => {
    if (!issue) return;
    const orig = document.title;
    let on = false;
    const t = window.setInterval(() => { on = !on; document.title = on ? "⚠ HealthSherpa login needed" : orig; }, 1000);
    return () => { window.clearInterval(t); document.title = orig; };
  }, [!!issue]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!issue || dismissed === key) return null;
  const since = issue.since ? Math.floor((Date.now() - new Date(issue.since).getTime()) / 60000) : 0;
  return (
    <div className="fixed inset-0 z-[9500] flex items-center justify-center p-5" style={{ background: "rgba(10,14,22,.55)" }}
         role="alertdialog" aria-modal="true" data-testid="hs-login-alert">
      <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl">
        <div className="text-3xl">🔐</div>
        <h2 className="mt-1 text-lg font-extrabold text-danger">{issue.title}</h2>
        <p className="mt-2 text-sm text-ink-soft">{issue.detail}</p>
        <p className="mt-2 text-xs text-ink-muted">
          Waiting {since} min{since === 1 ? "" : "s"} — the client stops after 15 minutes, and auto mode can't run anyone else until this is done.
          {meIsAgent ? " Switch to the HealthSherpa tab in this Chrome and log in." : " Tell the HealthSherpa agent to log in on their computer."}
        </p>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          {perm === "default" && (
            <button className="rounded-lg border border-hairline px-3 py-1.5 text-xs font-semibold text-ink-muted hover:bg-black/5"
                    onClick={() => Notification.requestPermission().then(setPerm).catch(() => {})}>
              🔔 Also alert me on the desktop
            </button>
          )}
          <button className="rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-white hover:bg-accent-hover"
                  onClick={() => setDismissed(key)}>OK</button>
        </div>
      </div>
    </div>
  );
}

/* AUTOMATION STATUS — the "full picture" at the top of the HealthSherpa text
 * campaigns tab, for admins and the HealthSherpa agent:
 *   - one headline: Working / Needs attention / Blocked
 *   - what is blocking or waiting (login needed, auto mode off, outside texting
 *     hours, paused, daily limits, needs reply, recent errors)
 *   - live counts per stage (click = filter the table) and today's totals
 *   - the HealthSherpa agent's auto mode (tab seen, extension version, client
 *     running now + step + time) and texting hours per state
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
} as const;

// ------------------------------------------------------------------ panel

const TILES: { key: string; label: string; hint: string }[] = [
  { key: "QUEUED", label: "Not texted yet", hint: "Waiting for the blast (texting hours / daily cap / pause)" },
  { key: "TEXTED", label: "Texted, no reply", hint: "Got the campaign text, haven't answered" },
  { key: "ASKED", label: "Asked company", hint: "Said yes — we asked which company they're renewing" },
  { key: "NEEDS_REPLY", label: "Needs reply", hint: "A person should answer them" },
  { key: "HS_QUEUED", label: "HealthSherpa queue", hint: "Answered — waiting for auto mode to run them" },
  { key: "HS_RUNNING", label: "Running now", hint: "The extension is working on them in HealthSherpa" },
  { key: "LINK_SENT", label: "Link sent", hint: "HealthSherpa link sent — waiting for the client to authorize (15 min)" },
  { key: "AUTHORIZED", label: "Authorized", hint: "Done ✔" },
  { key: "EXPIRED", label: "Expired", hint: "The 15-minute link ran out" },
  { key: "HS_REVIEW", label: "Needs a look", hint: "HealthSherpa run stopped — open the client for the reason" },
  { key: "DNC", label: "Opted out", hint: "STOP / no / wrong number — never texted again" },
];

export function AutomationPanel({ st, at, onStage, onOpen }: {
  st: HsStatus; at: number; onStage: (stage: string) => void; onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem("hsStatusOpen") !== "0"; } catch { return true; } });
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => { const t = window.setInterval(() => setNowMs(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  const toggle = () => { const v = !open; setOpen(v); try { localStorage.setItem("hsStatusOpen", v ? "1" : "0"); } catch { /* */ } };

  const lv = LEVEL[st.level];
  const headline = st.level === "error" ? "Blocked — needs action" : st.level === "warn" ? "Running — needs attention" : "Working normally";
  const errors = st.issues.filter((i) => i.level !== "info");
  const infos = st.issues.filter((i) => i.level === "info");
  const h = st.hours;
  const hoursLine = h.ignored ? { tone: "info" as const, text: "Texting hours ignored (test mode)" }
    : h.eastern_open && h.pacific_open ? { tone: "ok" as const, text: `Inside texting hours (${hourLabel(h.start_hour)} – ${hourLabel(h.end_hour)} client time) — blasts are going out` }
    : h.eastern_open ? { tone: "warn" as const, text: `Texting hours open for Eastern/Central — Pacific opens at ${hourLabel(h.start_hour)} PT` }
    : h.pacific_open ? { tone: "warn" as const, text: `Texting hours closed for Eastern (after ${hourLabel(h.end_hour)} ET) — still open out West` }
    : { tone: "error" as const, text: `OUTSIDE texting hours (${hourLabel(h.start_hour)} – ${hourLabel(h.end_hour)} client time) — campaign texts wait` };

  return (
    <section className={`rounded-2xl border p-4 ${lv.box}`} data-testid="hs-automation">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="relative flex h-3 w-3">
            {st.level !== "ok" && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${lv.dot}`} />}
            <span className={`relative inline-flex h-3 w-3 rounded-full ${lv.dot}`} />
          </span>
          <div>
            <div className="text-sm font-extrabold text-ink">Automation: <span className={lv.text} data-testid="hs-automation-level">{headline}</span></div>
            <div className="text-[0.7rem] text-ink-muted">
              Now {clock(st.now, "America/New_York")} ET · {clock(st.now, "America/Los_Angeles")} PT · your time {clock(new Date(nowMs).toISOString())}
              {" · "}updated {Math.max(0, Math.round((nowMs - at) / 1000))}s ago
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-3 py-1 text-[0.7rem] font-bold ${LEVEL[hoursLine.tone].box} ${LEVEL[hoursLine.tone].text} border`}
                data-testid="hs-hours-pill">
            🕗 {hoursLine.text}
          </span>
          <button onClick={toggle} className="rounded-lg px-2 py-1 text-xs font-semibold text-ink-muted hover:bg-black/5">
            {open ? "Hide details ▴" : "Show details ▾"}
          </button>
        </div>
      </div>

      {/* What's blocking / needs attention — always visible */}
      {errors.length > 0 && (
        <ul className="mt-3 space-y-1.5" data-testid="hs-issues">
          {errors.map((i, n) => (
            <li key={n} className={`flex flex-wrap items-start justify-between gap-2 rounded-lg border bg-white/70 px-3 py-2 ${LEVEL[i.level].box}`}>
              <div className="min-w-0 flex-1 text-xs">
                <div className={`font-bold ${LEVEL[i.level].text}`}>{LEVEL[i.level].icon} {i.title}
                  {i.since && <span className="ml-1 font-normal text-ink-faint">· since {dayClock(i.since)}</span>}</div>
                {i.detail && <div className="mt-0.5 text-ink-soft">{i.detail}</div>}
              </div>
              {i.request_id && <button onClick={() => onOpen(i.request_id!)} className="rounded-lg border border-hairline bg-white px-2 py-1 text-[0.7rem] font-semibold text-ink-muted hover:text-ink">Open client</button>}
              {i.code === "NEEDS_REPLY" && <button onClick={() => onStage("NEEDS_REPLY")} className="rounded-lg border border-hairline bg-white px-2 py-1 text-[0.7rem] font-semibold text-ink-muted hover:text-ink">Show them</button>}
            </li>
          ))}
        </ul>
      )}

      {/* Live pipeline */}
      <div className="mt-3 grid grid-cols-3 gap-1.5 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-11" data-testid="hs-pipeline">
        {TILES.map((t) => {
          const n = st.pipeline[t.key] || 0;
          const hot = n > 0 && ["NEEDS_REPLY", "HS_REVIEW"].includes(t.key);
          return (
            <button key={t.key} title={t.hint} onClick={() => onStage(t.key)} data-testid={`hs-tile-${t.key}`}
                    className={`rounded-lg border px-2 py-1.5 text-left hover:border-accent/50 ${hot ? "border-danger/30 bg-danger/5" : "border-hairline bg-white/70"}`}>
              <div className={`text-lg font-extrabold tabular-nums ${hot ? "text-danger" : n ? "text-ink" : "text-ink-faint"}`}>{n.toLocaleString()}</div>
              <div className="text-[0.66rem] font-semibold leading-tight text-ink-muted">{t.label}</div>
            </button>
          );
        })}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[0.72rem] text-ink-soft" data-testid="hs-today">
        <b className="text-ink">Today:</b>
        <span>Texts sent <b className="tabular-nums text-ink">{st.today.texts_sent}</b> / {st.today.daily_cap}</span>
        <span>Replied <b className="tabular-nums text-ink">{st.today.replies}</b></span>
        <span>Answered company <b className="tabular-nums text-ink">{st.today.answered}</b></span>
        <span>Links sent <b className="tabular-nums text-ink">{st.today.links_sent}</b></span>
        <span>Authorized <b className="tabular-nums text-success">{st.today.authorized}</b></span>
        <span>Stopped <b className={`tabular-nums ${st.today.stopped ? "text-danger" : "text-ink"}`}>{st.today.stopped}</b></span>
        <span>Opted out <b className="tabular-nums text-ink">{st.today.opted_out}</b></span>
      </div>

      {open && (
        <div className="mt-3 grid gap-3 lg:grid-cols-3">
          {/* Auto mode per HealthSherpa agent */}
          <div className="rounded-xl border border-hairline bg-white/70 p-3 text-xs">
            <div className="mb-1.5 text-[0.68rem] font-bold uppercase tracking-wide text-ink-faint">Auto mode (HealthSherpa)</div>
            {st.agents.length === 0 ? <div className="text-ink-faint">No campaign running yet.</div> : st.agents.map((a) => (
              <div key={a.id} className="space-y-1 border-b border-hairline-soft py-1.5 last:border-0" data-testid="hs-agent-card">
                <div className="flex items-center justify-between gap-2">
                  <b className="text-ink">{a.name}</b>
                  <span className={`rounded-full px-2 py-0.5 text-[0.66rem] font-bold ${a.alive ? "bg-success/15 text-success" : "bg-danger/10 text-danger"}`}>
                    {a.alive ? "● ON" : a.auto_on ? "● NOT SEEN" : "● OFF"}
                  </span>
                </div>
                <div className="text-ink-muted">
                  {a.seen_at ? <>Page last seen {ago(a.seen_at)}</> : "Never turned on"}
                  {a.ext_version && <> · extension v{a.ext_version}</>}
                  {a.alive && a.tab_hidden && <> · tab in background</>}
                </div>
                <div className="text-ink-muted">
                  Queue <b className="text-ink">{a.queue}</b> · HealthSherpa runs today <b className={a.usage.left ? "text-ink" : "text-danger"}>{a.usage.used}/{a.usage.limit}</b>
                </div>
                {a.running && (
                  <button onClick={() => onOpen(a.running!.id)}
                          className={`mt-1 block w-full rounded-lg px-2 py-1.5 text-left ${a.running.login ? "bg-danger/10 text-danger" : "bg-accent/10 text-accent"}`}>
                    <div className="font-bold">▶ {a.running.name}</div>
                    <div>{STEP[a.running.step || "start"] || a.running.step} · {a.running.status === "LINK_SENT"
                      ? <>link sent {mmss(a.running.link_sent_at, nowMs)} ago (expires at 15:00)</>
                      : <>running {mmss(a.running.started_at, nowMs)}</>}</div>
                    {a.running.detail && !a.running.login && <div className="truncate opacity-80">{a.running.detail}</div>}
                  </button>
                )}
              </div>
            ))}
          </div>

          {/* Texting hours per state */}
          <div className="rounded-xl border border-hairline bg-white/70 p-3 text-xs" data-testid="hs-hours">
            <div className="mb-1.5 text-[0.68rem] font-bold uppercase tracking-wide text-ink-faint">
              Campaign texts waiting · {hourLabel(h.start_hour)} – {hourLabel(h.end_hour)} client time
            </div>
            {h.states.length === 0 && !h.paused ? (
              <div className="text-ink-faint">Nothing waiting to be texted.</div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {h.states.map((s) => (
                  <span key={s.state} className={`rounded-full px-2 py-0.5 font-semibold ${s.open ? "bg-success/15 text-success" : "bg-black/5 text-ink-muted"}`}
                        title={s.open ? "Sending now" : s.opens_at ? `Opens ${dayClock(s.opens_at)} your time` : "Closed"}>
                    {s.state} · {s.waiting} · {s.open ? "sending" : s.opens_at ? `opens ${dayClock(s.opens_at)}` : "closed"}
                  </span>
                ))}
                {h.paused > 0 && <span className="rounded-full bg-pending/15 px-2 py-0.5 font-semibold text-pending">{h.paused} in paused campaigns</span>}
              </div>
            )}
            <div className="mt-2 text-[0.68rem] text-ink-faint">
              Only the first text (and re-texts) wait for hours. Replies, follow-ups and HealthSherpa links go any hour. Times shown in your time.
            </div>
            {infos.filter((i) => i.code !== "HOURS").map((i, n) => (
              <div key={n} className="mt-1.5 text-[0.7rem] text-accent">ℹ️ {i.title}{i.detail ? ` — ${i.detail}` : ""}</div>
            ))}
          </div>

          {/* Recent problems */}
          <div className="rounded-xl border border-hairline bg-white/70 p-3 text-xs" data-testid="hs-problems">
            <div className="mb-1.5 text-[0.68rem] font-bold uppercase tracking-wide text-ink-faint">Recent problems (24 h)</div>
            {st.recent_problems.length === 0 ? <div className="text-ink-faint">None 🎉</div> : (
              <ul className="space-y-1">
                {st.recent_problems.map((p) => (
                  <li key={p.id}>
                    <button onClick={() => onOpen(p.id)} className="block w-full rounded-lg px-1.5 py-1 text-left hover:bg-black/5">
                      <div className="flex justify-between gap-2"><b className="text-ink">{p.name}</b><span className="shrink-0 text-ink-faint">{dayClock(p.at)}</span></div>
                      <div className="text-danger">{PROBLEM[p.status] || p.status}{p.step && STEP[p.step] && p.step !== "done" ? ` (at “${STEP[p.step]}”)` : ""}</div>
                      {p.detail && <div className="line-clamp-2 text-ink-muted">{p.detail}</div>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

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

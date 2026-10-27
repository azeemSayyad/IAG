import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { getAccessToken } from "../lib/auth";
import { formatPhone } from "../lib/phone";
import { getSocket } from "../lib/socket";
import {
  MANUAL_LIVE,
  endManualCall,
  manualLive,
  muteManual,
  nudgeManual,
  onManual,
  resetManual,
  sendManualDigits,
  startManualCall,
  type ManualCall,
  type ManualState,
} from "../lib/voice";
import { Drawer, Field, drawerCtl } from "./Drawer";

/* Manual dialing — the Dial drawer. Mounted ONCE in PortalShell (so a call
   survives moving between SMS pages); pages only show a <DialButton/> that
   opens it. Auto Dialer page: manager-class. Lead Manager: agents.

   Two tabs:
   • Dial  — number + keypad + caller ID. In a call: status, timer, Mute,
             Hang up; the keypad then sends tones ("press 1 for…").
   • Calls — the agent's own manual calls (tap to redial); manager-class sees
             everyone's, with who dialed and the recording.

   Closing the drawer does NOT end a call — a small dock keeps it reachable. */

const OPEN_EVENT = "dial:open";
const FROM_KEY = "manualDialFrom";

export function openDialer(phone?: string) {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { phone } }));
}

export function DialButton({ className = "" }: { className?: string }) {
  return (
    <button
      onClick={() => openDialer()}
      title="Call any number"
      className={`flex h-9 items-center gap-1.5 rounded-lg border border-accent/25 bg-accent/10 px-3 text-sm font-semibold text-accent shadow-sm transition-all hover:-translate-y-px hover:bg-accent/15 hover:shadow-md ${className}`}
    >
      <IconPhone /> Dial
    </button>
  );
}

type Config = { available: boolean; simulate: boolean; numbers: string[]; default: string | null };
type Row = ManualCall & { user_id?: string; user_name?: string | null; recording?: "stored" | "pending" | "failed" | null };
type History = { items: Row[]; more: boolean; manager: boolean; users?: { id: string; name: string }[] };

const STATUS: Record<string, { label: string; tone: string }> = {
  DIALING: { label: "Calling", tone: "bg-accent/12 text-accent" },
  RINGING: { label: "Ringing", tone: "bg-accent/12 text-accent" },
  ANSWERED: { label: "Live", tone: "bg-success/15 text-success" },
  COMPLETED: { label: "Talked", tone: "bg-success/15 text-success" },
  NO_ANSWER: { label: "No answer", tone: "bg-pending/15 text-pending" },
  BUSY: { label: "Busy", tone: "bg-pending/15 text-pending" },
  FAILED: { label: "Failed", tone: "bg-danger/10 text-danger" },
  CANCELLED: { label: "Cancelled", tone: "bg-black/5 text-ink-muted" },
};

const KEYS: [string, string][] = [
  ["1", ""], ["2", "ABC"], ["3", "DEF"],
  ["4", "GHI"], ["5", "JKL"], ["6", "MNO"],
  ["7", "PQRS"], ["8", "TUV"], ["9", "WXYZ"],
  ["*", ""], ["0", ""], ["#", ""],
];

/** US numbers only: the 10 digits, anything past 10 ignored. A US area code
    never starts with 0 or 1, so a leading 1 is the country code ("+1 678…",
    "1678…") and leading 0/1 digits are dropped as they are typed. */
function usDigits(raw: string): string {
  return raw.replace(/\D/g, "").replace(/^[01]+/, "").slice(0, 10);
}

/** Formats as it is typed: (678) 988-7287. */
function formatTyping(d: string): string {
  if (!d) return "";
  if (d.length < 4) return `(${d}`;
  if (d.length < 7) return `(${d.slice(0, 3)}) ${d.slice(3)}`;
  return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
}

function dialable(d: string): boolean {
  return d.length === 10;
}

function mmss(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function since(iso: string | null): number {
  return iso ? (Date.now() - new Date(iso).getTime()) / 1000 : 0;
}

function when(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return today ? t : `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${t}`;
}

function loadFrom(): string {
  try {
    return localStorage.getItem(FROM_KEY) || "";
  } catch {
    return "";
  }
}

export default function DialDrawer() {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"dial" | "calls">("dial");
  const [num, setNum] = useState("");
  const [cfg, setCfg] = useState<Config | null>(null);
  const [from, setFrom] = useState(loadFrom);
  const [m, setM] = useState<ManualState>({ call: null, starting: false, error: null, simulated: false });
  const [muted, setMuted] = useState(false);
  const [tones, setTones] = useState("");
  const [, tick] = useState(0);

  useEffect(() => onManual(setM), []);

  useEffect(() => {
    const onOpen = (e: Event) => {
      const phone = (e as CustomEvent<{ phone?: string }>).detail?.phone;
      if (phone && !manualLive()) setNum(usDigits(phone));
      setTab("dial");
      setOpen(true);
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);

  // Caller IDs, once per opening (numbers can change between deploys).
  useEffect(() => {
    if (!open) return;
    api<Config>("/voice/manual/config")
      .then((c) => {
        setCfg(c);
        setFrom((f) => (f && c.numbers.includes(f) ? f : c.default || ""));
      })
      .catch(() => setCfg({ available: false, simulate: false, numbers: [], default: null }));
  }, [open]);

  useEffect(() => {
    const s = getSocket();
    const nudge = () => nudgeManual();
    s.on("voice:manual", nudge);
    return () => {
      s.off("voice:manual", nudge);
    };
  }, []);

  const call = m.call;
  const live = m.starting || (!!call && MANUAL_LIVE.includes(call.status));
  const talking = call?.status === "ANSWERED";

  // Call timer.
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => tick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [live]);

  useEffect(() => {
    if (!live) {
      setMuted(false);
      setTones("");
    }
  }, [live]);

  // Leaving the SMS app drops the call: warn, and open off-app links in a new tab.
  useEffect(() => {
    if (!live) return;
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const onClick = (e: MouseEvent) => {
      const a = (e.target as HTMLElement | null)?.closest?.("a");
      const href = a?.getAttribute("href") || "";
      if (!a || !href || href.startsWith("#") || a.target === "_blank") return;
      e.preventDefault();
      window.open(a.href, "_blank", "noopener");
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [live]);

  function pickFrom(v: string) {
    setFrom(v);
    try {
      localStorage.setItem(FROM_KEY, v);
    } catch {
      /* storage blocked */
    }
  }

  function press(k: string) {
    if (talking) {
      setTones((t) => (t + k).slice(-24));
      sendManualDigits(k);
    } else if (!live && /\d/.test(k)) {
      setNum((n) => usDigits(n + k));
    }
  }

  function dial(raw = num) {
    const target = usDigits(raw);
    if (live || !dialable(target)) return;
    setNum(target);
    setTab("dial");
    startManualCall(target, from || null);
  }

  async function toggleMute() {
    const next = !muted;
    await muteManual(next);
    setMuted(next);
  }

  const shownNumber = call?.phone_number || num;
  const statusLine = m.starting
    ? "Calling…"
    : call?.status === "DIALING"
      ? "Calling…"
      : call?.status === "RINGING"
        ? "Ringing…"
        : talking
          ? mmss(since(call?.answered_at ?? null))
          : call
            ? `${STATUS[call.status]?.label || call.status}${call.duration_seconds ? ` · ${mmss(call.duration_seconds)}` : ""}`
            : "";

  return (
    <>
      <Drawer
        open={open}
        title="Dial"
        sub={live ? "Closing this keeps the call going." : "Call any number from your computer."}
        icon="📞"
        onClose={() => setOpen(false)}
      >
        <div className="flex rounded-xl bg-black/5 p-1 text-sm font-semibold" role="tablist">
          {(["dial", "calls"] as const).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={`flex-1 rounded-lg py-1.5 transition ${tab === t ? "bg-white text-ink shadow-sm" : "text-ink-muted hover:text-ink"}`}
            >
              {t === "dial" ? "Dial" : "Calls"}
            </button>
          ))}
        </div>

        {tab === "dial" ? (
          <div className="space-y-4">
            {cfg && !cfg.available && (
              <div className="rounded-xl bg-danger/10 px-3.5 py-2.5 text-sm text-danger">
                Calling isn't set up yet (Vonage is not configured).
              </div>
            )}

            {live || call ? (
              /* ── In a call / just ended ── */
              <div className="rounded-2xl bg-black/5 px-4 py-5 text-center">
                <div className="text-2xl font-bold tracking-tight text-ink">{formatPhone(shownNumber)}</div>
                <div className={`mt-1 text-sm font-semibold tabular-nums ${talking ? "text-success" : live ? "text-accent" : "text-ink-muted"}`}>
                  {live && !talking && <span className="mr-1.5 inline-block h-2 w-2 animate-pulse rounded-full bg-accent align-middle" />}
                  {talking && <span className="mr-1.5 inline-block h-2 w-2 animate-pulse rounded-full bg-success align-middle" />}
                  {statusLine}
                </div>
                {call?.from_number && (
                  <div className="mt-1 text-xs text-ink-faint">From {formatPhone(call.from_number)}{m.simulated ? " · simulator" : ""}</div>
                )}
                {tones && <div className="mt-2 font-mono text-sm tracking-widest text-ink-muted">{tones}</div>}
              </div>
            ) : (
              <Field label="Number">
                <input
                  value={formatTyping(num)}
                  onChange={(e) => setNum(usDigits(e.target.value))}
                  onKeyDown={(e) => e.key === "Enter" && dial()}
                  inputMode="tel"
                  autoComplete="off"
                  placeholder="(555) 123-4567"
                  className={`${drawerCtl} text-center text-xl font-semibold tracking-wide`}
                />
              </Field>
            )}

            {m.error && <div className="rounded-xl bg-danger/10 px-3.5 py-2.5 text-sm text-danger">{m.error}</div>}

            {(!call || talking) && !m.starting && (
              <div className="mx-auto grid max-w-[300px] grid-cols-3 gap-2.5">
                {KEYS.map(([k, sub]) => (
                  <button
                    key={k}
                    onClick={() => press(k)}
                    disabled={(live && !talking) || (!live && !/\d/.test(k))}
                    className="flex h-14 flex-col items-center justify-center rounded-2xl border border-hairline bg-white text-ink transition hover:border-accent/40 hover:bg-accent/5 active:scale-95 disabled:opacity-40"
                  >
                    <span className="text-xl font-semibold leading-none">{k}</span>
                    {sub && <span className="mt-0.5 text-[9px] font-semibold tracking-widest text-ink-faint">{sub}</span>}
                  </button>
                ))}
              </div>
            )}

            {live ? (
              <div className="flex justify-center gap-3">
                {talking && !m.simulated && (
                  <button
                    onClick={toggleMute}
                    className="h-12 rounded-full border border-hairline px-5 text-sm font-semibold text-ink-muted hover:bg-black/5"
                  >
                    {muted ? "Unmute" : "Mute"}
                  </button>
                )}
                <button
                  onClick={() => endManualCall()}
                  className="flex h-12 items-center gap-2 rounded-full bg-danger px-7 text-sm font-bold text-white shadow-md hover:brightness-110"
                >
                  <IconPhone className="rotate-[135deg]" /> Hang up
                </button>
              </div>
            ) : call ? (
              <div className="flex justify-center gap-3">
                <button
                  onClick={() => dial(call.phone_number)}
                  className="flex h-11 items-center gap-2 rounded-full bg-success px-6 text-sm font-bold text-white shadow-md hover:brightness-110"
                >
                  <IconPhone /> Call again
                </button>
                <button
                  onClick={() => {
                    resetManual();
                    setNum("");
                  }}
                  className="h-11 rounded-full border border-hairline px-5 text-sm font-semibold text-ink-muted hover:bg-black/5"
                >
                  New number
                </button>
              </div>
            ) : (
              <>
                <div className="flex items-center justify-center gap-3">
                  <button
                    onClick={() => dial()}
                    disabled={!dialable(num) || !cfg?.available || !from}
                    className="flex h-12 items-center gap-2 rounded-full bg-success px-8 text-base font-bold text-white shadow-md transition hover:brightness-110 disabled:opacity-40"
                  >
                    <IconPhone /> Call
                  </button>
                  {num && (
                    <button
                      onClick={() => setNum((n) => n.slice(0, -1))}
                      aria-label="Delete last digit"
                      className="grid h-12 w-12 place-items-center rounded-full text-ink-muted hover:bg-black/5"
                    >
                      ⌫
                    </button>
                  )}
                </div>
                <Field label="Caller ID" hint="The number they'll see. Remembered on this computer.">
                  <select value={from} onChange={(e) => pickFrom(e.target.value)} className={drawerCtl}>
                    {(cfg?.numbers || []).map((n) => (
                      <option key={n} value={n}>
                        {formatPhone(n)}
                        {n === cfg?.default ? " (default)" : ""}
                      </option>
                    ))}
                  </select>
                </Field>
              </>
            )}
          </div>
        ) : (
          <CallsTab onRedial={(p) => (live ? undefined : dial(p))} live={live} />
        )}
      </Drawer>

      {/* Mini dock — the call stays reachable with the drawer closed. */}
      {!open && live && (
        <div className="fixed bottom-4 right-4 z-[9400] flex max-w-[calc(100vw-2rem)] items-center gap-2.5 rounded-xl border border-hairline bg-white px-3.5 py-2.5 text-sm text-ink shadow-xl">
          <span className={`h-2.5 w-2.5 flex-shrink-0 animate-pulse rounded-full ${talking ? "bg-success" : "bg-accent"}`} />
          <button onClick={() => setOpen(true)} className="min-w-0 truncate font-semibold hover:underline">
            {formatPhone(shownNumber)}
          </button>
          <span className="tabular-nums text-ink-muted">{statusLine}</span>
          <button
            onClick={() => endManualCall()}
            className="rounded-lg bg-danger px-2.5 py-1 text-xs font-semibold text-white"
          >
            Hang up
          </button>
        </div>
      )}
    </>
  );
}

function CallsTab({ onRedial, live }: { onRedial: (phone: string) => void; live: boolean }) {
  const [data, setData] = useState<History | null>(null);
  const [who, setWho] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(
    async (more = false) => {
      setLoading(true);
      setErr("");
      try {
        const qs = new URLSearchParams({ limit: "30" });
        if (who) qs.set("user_id", who);
        const last = more ? data?.items[data.items.length - 1]?.created_at : null;
        if (last) qs.set("before", last);
        const page = await api<History>(`/voice/manual/calls?${qs}`);
        setData((d) => (more && d ? { ...page, items: [...d.items, ...page.items] } : page));
      } catch {
        setErr("Couldn't load calls.");
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [who, data?.items.length],
  );

  useEffect(() => {
    load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [who]);

  // A call that just ended shows up here.
  useEffect(() => {
    if (!live) load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  const manager = !!data?.manager;

  return (
    <div className="space-y-3">
      {manager && (
        <select value={who} onChange={(e) => setWho(e.target.value)} className={drawerCtl} aria-label="Who dialed">
          <option value="">Everyone</option>
          {(data?.users || []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
            </option>
          ))}
        </select>
      )}
      {err && <div className="text-sm text-danger">{err}</div>}
      {data && data.items.length === 0 && (
        <div className="rounded-xl bg-black/5 px-4 py-8 text-center text-sm text-ink-muted">No calls yet.</div>
      )}
      <ul className="divide-y divide-hairline overflow-hidden rounded-2xl border border-hairline">
        {data?.items.map((c) => {
          const st = STATUS[c.status] || { label: c.status, tone: "bg-black/5 text-ink-muted" };
          return (
            <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 bg-white px-3.5 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-ink">{formatPhone(c.phone_number)}</div>
                <div className="truncate text-xs text-ink-faint">
                  {manager && c.user_name ? `${c.user_name} · ` : ""}
                  {when(c.created_at)}
                  {c.duration_seconds ? ` · ${mmss(c.duration_seconds)}` : ""}
                </div>
              </div>
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${st.tone}`}>{st.label}</span>
              {manager ? (
                <ManualRecording id={c.id} status={c.recording ?? null} />
              ) : (
                <button
                  onClick={() => onRedial(c.phone_number)}
                  disabled={live}
                  title="Call this number"
                  className="grid h-8 w-8 place-items-center rounded-full bg-success/15 text-success hover:bg-success/25 disabled:opacity-40"
                >
                  <IconPhone />
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {data?.more && (
        <button
          onClick={() => load(true)}
          disabled={loading}
          className="w-full rounded-xl border border-hairline py-2 text-sm font-semibold text-ink-muted hover:bg-black/5 disabled:opacity-50"
        >
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}

/** Play a manual call's recording (manager-class). <audio> can't send
    headers, so the token rides in the URL — same as the dialer's player. */
function ManualRecording({ id, status }: { id: string; status: "stored" | "pending" | "failed" | null }) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  if (status === "pending") return <span className="text-xs text-ink-faint">Saving…</span>;
  if (status === "failed") return <span className="text-xs text-danger">Not saved</span>;
  if (status !== "stored") return null;
  const src = `/api/v1/voice/manual/calls/${id}/recording?token=${encodeURIComponent(getAccessToken() || "")}`;
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        title="Play recording"
        className="grid h-8 w-8 place-items-center rounded-full bg-accent text-white hover:brightness-110"
      >
        <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor">
          <path d="M7 4.5v15l12-7.5z" />
        </svg>
      </button>
    );
  }
  if (failed) {
    return (
      <a href={src} target="_blank" rel="noopener" className="text-xs font-semibold text-accent underline">
        Open recording
      </a>
    );
  }
  return <audio controls autoPlay src={src} onError={() => setFailed(true)} className="h-8 w-full" />;
}

function IconPhone({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.2"
         strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z" />
    </svg>
  );
}

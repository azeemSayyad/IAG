import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { JEWEL_MID, agentColor } from "../components/charts/jewel";
import DealsByAgentDonut from "../components/charts/DealsByAgentDonut";
import WeeklyBars3D from "../components/charts/WeeklyBars3D";

/* ── Types (mirror /api/v1/sales-dashboard/overview) ─────────────────────── */
type AgentRow = { agent_name: string; deals: number; members: number };
type Overview = {
  range: { from: string; to: string };
  agents: AgentRow[];
  deals_total: number;
  leads_total: number;
  sales_mix: { applications: number; members: number; medical: number; dental: number; vision: number };
  carrier_mix: { carrier: string; deals: number }[];
  weekly: { label: string; date: string; deals: number; members: number }[];
  weekly_granularity?: "day" | "week" | "month" | "year" | "decade";
  calendar: { start_time: string | null; customer: string; agent_name: string }[];
  recent_conversations: { phone_number: string; agent_name: string | null; created_at: string | null }[];
  recent_applications: {
    customer_name: string; agent_name: string; status: string | null; won: boolean; product: string; members: number; created_at: string | null;
  }[];
};

// Leaderboard rows (/compliance/deals/leaderboard) — the SAME source the static
// Leaderboard page uses, so the "Deals by agent" donut matches it exactly. A "deal"
// there = ACA + Dental + Vision (coverage), not the deal-row count the overview returns.
type LbRow = { agent_name: string; total_aca?: number; total_dental?: number; total_vision?: number };
type LbResp = { leaderboard?: LbRow[] };

const PRODUCT_TONE: Record<string, string> = {
  Medical: "bg-[#3B82F6]/14 text-[#1d4ed8]",
  Dental: "bg-[#10B981]/16 text-[#047857]",
  Vision: "bg-[#F43F5E]/14 text-[#be123c]",
};

/* ── Helpers ─────────────────────────────────────────────────────────────── */

// ---- Date range presets (computed in Florida / Eastern, matching backend) ----
const BIZ_TZ = "America/New_York";
// Today's date as YYYY-MM-DD in the Florida business timezone (en-CA => ISO).
function todayET(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: BIZ_TZ }).format(new Date());
}
// Shift a YYYY-MM-DD by N days (pure date math, tz-agnostic).
function shiftISO(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}
// "Tuesday, June 16, 2026" (parsed as a local date so it never shifts a day).
function prettyDate(iso: string): string {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", {
    weekday: "long", month: "long", day: "numeric", year: "numeric",
  });
}
// "06/22/2026" from a YYYY-MM-DD ISO date.
function fmtMD(iso: string): string {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `${m}/${d}/${y}`;
}
type PresetKey = "today" | "yesterday" | "this_week" | "this_month" | "this_year" | "all" | "custom";
const PRESETS: { key: PresetKey; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "this_week", label: "This week" },
];
// Resolve a preset to a {from, to} pair (YYYY-MM-DD, Eastern).
function presetRange(key: PresetKey): { from: string; to: string } {
  const t = todayET();
  const [yy, mm, dd] = t.split("-").map(Number);
  switch (key) {
    case "yesterday": { const y = shiftISO(t, -1); return { from: y, to: y }; }
    case "this_week": {  // calendar week: Monday -> today
      const offset = (new Date(Date.UTC(yy, mm - 1, dd)).getUTCDay() + 6) % 7;
      return { from: shiftISO(t, -offset), to: t };
    }
    case "this_month": return { from: `${yy}-${String(mm).padStart(2, "0")}-01`, to: t }; // CALENDAR month-to-date
    case "this_year": return { from: `${yy}-01-01`, to: t };                               // calendar year-to-date
    case "all": return { from: "2000-01-01", to: t };
    case "today":
    default: return { from: t, to: t };
  }
}

function Panel({ title, sub, children, right }: { title: string; sub?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="glass rounded-2xl p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-bold text-ink">{title}</h3>
          {sub && <p className="text-xs text-ink-muted">{sub}</p>}
        </div>
        {right}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}


export default function SalesDashboard() {
  const [ov, setOv] = useState<Overview | null>(null);
  // Deals-by-agent donut is sourced from the leaderboard endpoint (see LbRow) so it
  // matches the Leaderboard page's per-agent + team totals exactly.
  const [board, setBoard] = useState<LbRow[]>([]);
  // Daily Snapshot bars use a rolling last-7-days window, independent of the date
  // filter, so the past days always show real data (not just the selected day).
  const [snapOv, setSnapOv] = useState<Overview | null>(null);
  // Collapsible "Deals by agent + Carrier Mix" breakdown inside the Sales Mix container.
  const [breakdownOpen, setBreakdownOpen] = useState(false);
  const [preset, setPreset] = useState<PresetKey>("today");
  const [from, setFrom] = useState(() => presetRange("today").from);
  const [to, setTo] = useState(() => presetRange("today").to);
  // Custom date popover (matches the All Deals / Leaderboard date picker).
  const [customOpen, setCustomOpen] = useState(false);
  const [cFrom, setCFrom] = useState(from);
  const [cTo, setCTo] = useState(to);

  // Apply a preset: set the range and let the load effect refetch.
  const applyPreset = (key: PresetKey) => {
    setPreset(key);
    if (key !== "custom") { const r = presetRange(key); setFrom(r.from); setTo(r.to); }
  };

  const load = useMemo(
    () => () => {
      const qs = new URLSearchParams();
      if (from) qs.set("from", from);
      if (to) qs.set("to", to);
      qs.set("_", String(Date.now()));
      api<Overview>(`/sales-dashboard/overview?${qs.toString()}`)
        .then((r) => {
          setOv(r);
          if (!from && r?.range?.from) setFrom(r.range.from);
          if (!to && r?.range?.to) setTo(r.range.to);
        })
        .catch(() => { /* keep previous data */ });
    },
    [from, to],
  );

  useEffect(() => {
    load();
    const t = window.setInterval(load, 30_000);
    return () => window.clearInterval(t);
  }, [load]);

  // "Deals by agent" donut — fetch the SAME leaderboard data the Leaderboard page uses
  // (same date range), so per-agent and team totals match it exactly.
  useEffect(() => {
    const loadBoard = () => {
      const qs = new URLSearchParams();
      if (from) qs.set("from", from);
      if (to) qs.set("to", to);
      qs.set("_", String(Date.now()));
      api<LbResp>(`/compliance/deals/leaderboard?${qs.toString()}`)
        .then((r) => setBoard(r?.leaderboard ?? []))
        .catch(() => { /* keep previous */ });
    };
    loadBoard();
    const t = window.setInterval(loadBoard, 30_000);
    return () => window.clearInterval(t);
  }, [from, to]);

  // Daily Snapshot — always show THIS WEEK's daily bars (so the past days have real
  // data), regardless of the Today / Yesterday / This-week filter. Only "Custom"
  // follows the selected date range.
  useEffect(() => {
    const loadSnap = () => {
      const r = preset === "custom" ? { from, to } : presetRange("this_week");
      const qs = new URLSearchParams({ from: r.from, to: r.to, _: String(Date.now()) });
      api<Overview>(`/sales-dashboard/overview?${qs.toString()}`).then(setSnapOv).catch(() => {});
    };
    loadSnap();
    const id = window.setInterval(loadSnap, 30_000);
    return () => window.clearInterval(id);
  }, [preset, from, to]);

  // Per-agent "deals" = ACA + Dental + Vision (coverage), matching the Leaderboard page
  // exactly. Every agent with >0 shown, sorted desc, each a distinct colour via agentColor.
  const donutBreakdown = useMemo(
    () => [...board]
      .map((a) => ({ name: a.agent_name, deals: (a.total_aca || 0) + (a.total_dental || 0) + (a.total_vision || 0) }))
      .filter((a) => a.deals > 0)
      .sort((a, b) => b.deals - a.deals)
      .map((a, i) => ({ name: a.name, deals: a.deals, color: agentColor(i) })),
    [board],
  );
  // Plot deal-ROW COUNT per bucket (ties to Total Deals). The backend range-filters
  // and auto-scales the buckets, so the snapshot tracks the selected date filter.
  // Snapshot title/subtitle reflect the SELECTED range + bucket granularity
  // (the bars are scoped to the same filter as the rest of the board).
  const snapGran = snapOv?.weekly_granularity ?? "day";
  const snapTitle =
    snapGran === "decade" ? "Snapshot by Decade"
    : snapGran === "year" ? "Yearly Snapshot"
    : snapGran === "month" ? "Monthly Snapshot"
    : snapGran === "week" ? "Weekly Snapshot"
    : "Daily Snapshot";
  // The snapshot's own range (this week, or the custom range).
  const snapRange = preset === "custom" ? { from, to } : presetRange("this_week");
  const snapSub =
    "Deals · " + (snapRange.from === snapRange.to ? prettyDate(snapRange.from) : `${prettyDate(snapRange.from)} → ${prettyDate(snapRange.to)}`);

  const weekData = useMemo(() => {
    let arr = (snapOv?.weekly ?? []).map((d) => ({ day: d.label, count: d.deals }));
    // Pad the front so a very short custom range still reads as a chart (≥5 bars).
    if (arr.length < 5) {
      const firstDate = snapOv?.weekly?.[0]?.date || todayET();
      const [y, m, d] = firstDate.split("-").map(Number);
      const base = new Date(y, m - 1, d);
      const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
      const pad = [];
      for (let i = 5 - arr.length; i >= 1; i--) {
        const dt = new Date(base);
        dt.setDate(base.getDate() - i);
        pad.push({ day: DOW[dt.getDay()], count: 0 });
      }
      arr = pad.concat(arr);
    }
    return arr;
  }, [snapOv]);
  const mix = ov?.sales_mix ?? { applications: 0, members: 0, medical: 0, dental: 0, vision: 0 };
  // Total deals = ACA + Dental + Vision (each coverage type counts as 1), computed
  // client-side so the headline always equals its breakdown. No backend change.
  const dealsTotal = (mix.medical || 0) + (mix.dental || 0) + (mix.vision || 0);
  const mixMax = Math.max(mix.medical, mix.dental, mix.vision, 1);
  const mixRows = [
    { id: "medical", name: "Medical", count: mix.medical },
    { id: "dental", name: "Dental", count: mix.dental },
    { id: "vision", name: "Vision", count: mix.vision },
  ];
  const carriers = ov?.carrier_mix ?? [];
  const carrierMax = Math.max(...carriers.map((c) => c.deals), 1);

  return (
    <div className="w-full px-1 pb-10">
      {/* Header */}
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight text-ink">Sales Dashboard</h1>
            {/* Quick access to the Agent performance page — opens it as a full page like any other. */}
            <button type="button" onClick={() => { window.location.href = "/agent-performance.html"; }}
              title="Go to Agent performance"
              className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink-muted transition hover:text-accent">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 text-accent"><path d="M7 16H3m0 0 4-4m-4 4 4 4"/><path d="M17 8h4m0 0-4-4m4 4-4 4"/></svg>
              Agent performance
            </button>
          </div>
          <p className="mt-1 text-sm text-ink-muted">
            {from === to ? prettyDate(from) : `${prettyDate(from)} → ${prettyDate(to)}`} · Eastern
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          {/* Total Deals KPI — single row, same height as the date pill */}
          <div className="flex items-center gap-2 self-stretch rounded-xl border border-hairline bg-white/60 px-4 shadow-sm">
            <span className="text-xs font-semibold uppercase tracking-wide text-ink-muted">Total Deals</span>
            <span className="text-sm font-extrabold tabular-nums text-ink">{dealsTotal}</span>
          </div>
          {/* Segmented date-range pill — consistent with All Deals / Leaderboard */}
          <div className="inline-flex flex-wrap items-center gap-1 rounded-xl border border-hairline bg-white/60 p-1 shadow-sm">
            {PRESETS.map((p) => (
              <button key={p.key} type="button" onClick={() => applyPreset(p.key)}
                className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
                  preset === p.key ? "bg-accent text-white shadow-sm" : "text-ink-muted hover:bg-white hover:text-ink"
                }`}>
                {p.label}
              </button>
            ))}
            <div className="relative">
              <button type="button" onClick={() => { setCFrom(from); setCTo(to); setCustomOpen((o) => !o); }}
                className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
                  preset === "custom" ? "bg-accent text-white shadow-sm" : "text-ink-muted hover:bg-white hover:text-ink"
                }`}>
                {preset === "custom" ? `${fmtMD(from)} → ${fmtMD(to)}` : "Custom"}
              </button>
              {customOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setCustomOpen(false)} />
                  <div className="absolute right-0 top-full z-50 mt-2 flex flex-col gap-4 rounded-2xl border border-hairline bg-white p-4 shadow-xl">
                    <div className="flex gap-3">
                      <label className="flex flex-col gap-1.5 text-[11px] font-bold uppercase tracking-wide text-ink-muted">From
                        <input type="date" value={cFrom} onChange={(e) => setCFrom(e.target.value)}
                          className="h-10 rounded-lg border border-hairline bg-white px-3 text-sm text-ink" />
                      </label>
                      <label className="flex flex-col gap-1.5 text-[11px] font-bold uppercase tracking-wide text-ink-muted">To
                        <input type="date" value={cTo} onChange={(e) => setCTo(e.target.value)}
                          className="h-10 rounded-lg border border-hairline bg-white px-3 text-sm text-ink" />
                      </label>
                    </div>
                    <div className="flex justify-end">
                      <button type="button" onClick={() => { setPreset("custom"); setFrom(cFrom); setTo(cTo); setCustomOpen(false); }}
                        className="rounded-lg bg-accent px-5 py-2 text-xs font-bold text-white shadow-sm transition hover:bg-accent-hover">
                        Apply
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-1 gap-4">
          {/* Combined container: Sales Mix on top, then Deals by agent (left) + Carrier Mix (right) */}
          <div className="glass rounded-2xl p-4">
            {/* Sales Mix header + breakdown toggle (secondary outlined CTA) */}
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-base font-bold text-ink">Sales Mix</h3>
                <p className="text-xs text-ink-muted">Deals by product</p>
              </div>
              <button type="button" onClick={() => setBreakdownOpen((o) => !o)}
                className="inline-flex items-center gap-2 rounded-lg border border-accent/40 px-4 py-2 text-xs font-bold text-accent shadow-sm transition hover:bg-accent/10"
                aria-expanded={breakdownOpen}>
                View deals by agent · carrier mix
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round"
                  className={`h-4 w-4 transition-transform duration-300 ${breakdownOpen ? "rotate-180" : ""}`}><path d="m6 9 6 6 6-6" /></svg>
              </button>
            </div>
            {/* Sales Mix product bars */}
            <div className="mt-5 flex flex-col gap-4">
              {mixRows.map((row) => (
                <div key={row.id} className="grid items-center gap-3" style={{ gridTemplateColumns: "68px 1fr auto" }}>
                  <span className="text-xs font-semibold text-ink-soft">{row.name}</span>
                  <div className="sd-pb-bar">
                    <div className={`sd-pb-fill ${row.id}`} style={{ width: `${Math.round((row.count / mixMax) * 100)}%` }} />
                  </div>
                  <span className="min-w-[28px] text-right text-sm font-extrabold tabular-nums text-ink">{row.count}</span>
                </div>
              ))}
            </div>
            {/* Collapsible breakdown — smooth slide-open */}
            <div className={`grid overflow-hidden transition-all duration-500 ease-out ${breakdownOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
              <div className="min-h-0">
                <div className="mt-10 grid grid-cols-1 gap-10 pt-4 lg:grid-cols-2">
              <div>
                <h3 className="text-base font-bold text-ink">Deals by agent</h3>
                <p className="text-xs text-ink-muted">Share of total deals</p>
                <div className="mt-5">
                  {donutBreakdown.length ? <DealsByAgentDonut key={`${from}_${to}`} breakdown={donutBreakdown} />
                    : <div className="py-10 text-center text-sm text-ink-faint">No deals in range.</div>}
                </div>
              </div>
              <div>
                <h3 className="text-base font-bold text-ink">Carrier Mix</h3>
                <p className="text-xs text-ink-muted">Deals by carrier</p>
                <div className="mt-5">
                  {carriers.length === 0 ? (
                    <div className="py-10 text-center text-sm text-ink-faint">No deals in range.</div>
                  ) : (
                    <div className="flex flex-col gap-3.5">
                      {carriers.map((c, i) => (
                        <div key={c.carrier} className="grid items-center gap-3" style={{ gridTemplateColumns: "minmax(72px,34%) 1fr auto" }}>
                          <span className="truncate text-xs font-semibold text-ink-soft" title={c.carrier}>{c.carrier}</span>
                          <div className="sd-pb-bar">
                            <div className="sd-pb-fill" style={{ width: `${Math.round((c.deals / carrierMax) * 100)}%`, background: JEWEL_MID[i % JEWEL_MID.length] }} />
                          </div>
                          <span className="min-w-[28px] text-right text-sm font-extrabold tabular-nums text-ink">{c.deals}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
                </div>
              </div>
            </div>
          </div>

          {/* Weekly Snapshot — 3D bars (full width) */}
          <Panel title={snapTitle} sub={snapSub}>
            <WeeklyBars3D data={weekData} />
          </Panel>
        </div>
    </div>
  );
}
